"""WaitlistEntryCoordinator."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Literal

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    BsportAuthError,
    BsportBookError,
    BsportClient,
    BsportTransientError,
    WaitlistEntry,
)
from .const import (
    DOMAIN,
    EVENT_AUTH_FAILED,
    EVENT_BOOK_FAILED,
    EVENT_BOOK_SUCCEEDED,
    EVENT_SPOT_OPEN,
    EVENT_WAITLIST_DISCARDED,
    SCAN_JITTER_RATIO,
    WAITLIST_INTERVAL_BEYOND_24H,
    WAITLIST_INTERVAL_UNDER_24H,
    WAITLIST_INTERVAL_UNDER_2H,
)

_LOGGER = logging.getLogger(__name__)

_2H = 2 * 3600
_24H = 24 * 3600


def _select_cadence(start_at: datetime) -> object:
    """Return a timedelta for the polling interval based on time-to-start."""
    now = datetime.now(timezone.utc)
    delta_secs = (start_at - now).total_seconds()

    if delta_secs <= _2H:
        base = WAITLIST_INTERVAL_UNDER_2H
    elif delta_secs <= _24H:
        base = WAITLIST_INTERVAL_UNDER_24H
    else:
        base = WAITLIST_INTERVAL_BEYOND_24H

    jitter = base * SCAN_JITTER_RATIO * random.random()
    return base + jitter


_BATCH_CACHE_TTL = timedelta(seconds=5)


class WaitlistBatchCache:
    """Shared cache for `list_waitlists_with_positions` across per-offer coords.

    Per-offer `WaitlistEntryCoordinator`s have their own adaptive cadence but
    tend to tick in sync (same base interval, same class-time neighbourhood).
    This cache coalesces concurrent ticks so N coordinators produce at most
    one pair of HTTP requests per `_BATCH_CACHE_TTL` window. Stale data just
    triggers a refresh on the next caller.

    Not thread-safe by design — HA runs the event loop single-threaded, so
    an asyncio.Lock is enough to serialize the refresh path.
    """

    def __init__(self, client: BsportClient) -> None:
        self._client = client
        self._lock = asyncio.Lock()
        self._cache: tuple[WaitlistEntry, ...] | None = None
        self._cache_at: datetime | None = None

    async def get_entry(self, offer_id: int) -> WaitlistEntry | None:
        entries = await self._refresh_if_stale()
        return next(
            (e for e in entries if e.offer.offer_id == offer_id), None,
        )

    def invalidate(self) -> None:
        """Drop the cached snapshot so the next getter fetches fresh."""
        self._cache = None
        self._cache_at = None

    async def _refresh_if_stale(self) -> tuple[WaitlistEntry, ...]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            fresh = (
                self._cache is not None
                and self._cache_at is not None
                and (now - self._cache_at) <= _BATCH_CACHE_TTL
            )
            if fresh:
                return self._cache  # type: ignore[return-value]
            self._cache = await self._client.list_waitlists_with_positions()
            self._cache_at = now
            return self._cache


class WaitlistEntryCoordinator(DataUpdateCoordinator[WaitlistEntry]):
    """Tracks a single waitlist entry, adapts cadence, fires spot-open events."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BsportClient,
        entry_id: str,
        initial: WaitlistEntry,
        batch_cache: WaitlistBatchCache,
    ):
        self._initial = initial
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_waitlist_{initial.offer.offer_id}",
            update_interval=_select_cadence(initial.offer.start_at),
        )
        self._client = client
        self._batch = batch_cache
        self.entry_id = entry_id

    async def _async_update_data(self) -> WaitlistEntry:
        try:
            new_entry = await self._batch.get_entry(
                self._initial.offer.offer_id
            )
        except BsportAuthError as err:
            self.hass.bus.async_fire(
                EVENT_AUTH_FAILED,
                {"entry_id": self.entry_id, "email": self._client_email()},
            )
            raise ConfigEntryAuthFailed(str(err)) from err
        except BsportTransientError as err:
            raise UpdateFailed(str(err)) from err

        if new_entry is None:
            raise UpdateFailed("waitlist entry disappeared")

        previous = self.data
        if (
            previous is not None
            and previous.status != "convertible"
            and new_entry.status == "convertible"
        ):
            offer = new_entry.offer
            self.hass.bus.async_fire(
                EVENT_SPOT_OPEN,
                {
                    "entry_id": self.entry_id,
                    "offer_id": offer.offer_id,
                    "class_name": offer.class_name,
                    "category": offer.category,
                    "coach": offer.coach,
                    "start_at": offer.start_at.isoformat(),
                    "position_was": previous.position,
                },
            )

        self.update_interval = _select_cadence(new_entry.offer.start_at)
        return new_entry

    async def async_book(
        self, *, source: Literal["waitlist", "watch", "service"]
    ) -> None:
        """Attempt to book the waitlist offer.

        bsport has no documented "convert waitlist → booking" endpoint. When
        the spot is convertible, the class is still marked full on the
        schedule because the open spot is held by the waitlist reservation,
        so `book_offer` returns 423 `cannot_book`. Workaround: when the
        coordinator sees we're in `convertible` state, drop the waitlist
        entry to release the held spot, then retry the book. Only runs once;
        a second failure is reported as-is.
        """
        entry = self.data if self.data is not None else self._initial
        offer = entry.offer
        offer_id = offer.offer_id
        try:
            await self._client.book_offer(offer_id)
        except BsportBookError as err:
            can_retry = (
                err.reason == "cannot_book"
                and self.data is not None
                and self.data.status == "convertible"
            )
            if can_retry:
                try:
                    await self._client.discard_waitlist(entry.entry_id)
                except BsportBookError:
                    # Couldn't drop the reservation — surface the original
                    # book error rather than hide it behind a new one.
                    self._fire_book_failed(offer, source, err.reason)
                    raise err
                try:
                    await self._client.book_offer(offer_id)
                except BsportBookError as err2:
                    self._fire_book_failed(offer, source, err2.reason)
                    raise
            else:
                self._fire_book_failed(offer, source, err.reason)
                raise
        self.hass.bus.async_fire(
            EVENT_BOOK_SUCCEEDED,
            {
                "entry_id": self.entry_id,
                "offer_id": offer_id,
                "class_name": offer.class_name,
                "start_at": offer.start_at.isoformat(),
                "source": source,
            },
        )
        await self.async_request_refresh()

    def _fire_book_failed(
        self, offer, source: str, reason: str,
    ) -> None:
        self.hass.bus.async_fire(
            EVENT_BOOK_FAILED,
            {
                "entry_id": self.entry_id,
                "offer_id": offer.offer_id,
                "class_name": offer.class_name,
                "reason": reason,
                "source": source,
            },
        )

    async def async_discard(self) -> None:
        """Leave the waitlist queue for this offer."""
        entry = self.data if self.data is not None else self._initial
        offer = entry.offer
        await self._client.discard_waitlist(entry.entry_id)
        # Invalidate the shared cache so the next poll observes the drop.
        self._batch.invalidate()
        self.hass.bus.async_fire(
            EVENT_WAITLIST_DISCARDED,
            {
                "entry_id": self.entry_id,
                "offer_id": offer.offer_id,
                "class_name": offer.class_name,
                "start_at": offer.start_at.isoformat(),
            },
        )

    def _client_email(self) -> str:
        return getattr(self._client, "_email", "")  # noqa: SLF001
