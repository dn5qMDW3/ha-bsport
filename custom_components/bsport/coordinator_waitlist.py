"""WaitlistEntryCoordinator."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
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


class WaitlistEntryCoordinator(DataUpdateCoordinator[WaitlistEntry]):
    """Tracks a single waitlist entry, adapts cadence, fires spot-open events."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BsportClient,
        entry_id: str,
        initial: WaitlistEntry,
    ):
        self._initial = initial
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_waitlist_{initial.offer.offer_id}",
            update_interval=_select_cadence(initial.offer.start_at),
        )
        self._client = client
        self.entry_id = entry_id

    async def _async_update_data(self) -> WaitlistEntry:
        try:
            new_entry = await self._client.get_waitlist_entry(
                offer_id=self._initial.offer.offer_id
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
        """Attempt to book the waitlist offer."""
        offer = self._initial.offer
        offer_id = offer.offer_id
        try:
            await self._client.book_offer(offer_id)
        except BsportBookError as err:
            self.hass.bus.async_fire(
                EVENT_BOOK_FAILED,
                {
                    "entry_id": self.entry_id,
                    "offer_id": offer_id,
                    "class_name": offer.class_name,
                    "reason": err.reason,
                    "source": source,
                },
            )
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

    async def async_discard(self) -> None:
        """Leave the waitlist queue for this offer."""
        entry = self.data if self.data is not None else self._initial
        offer = entry.offer
        await self._client.discard_waitlist(entry.entry_id)
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
