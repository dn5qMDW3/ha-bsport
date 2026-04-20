"""WatchedClassCoordinator."""
from __future__ import annotations

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
    Offer,
    WatchedClass,
    WatchStatus,
)
from .const import (
    DOMAIN,
    EVENT_AUTH_FAILED,
    EVENT_BOOK_FAILED,
    EVENT_BOOK_SUCCEEDED,
    EVENT_CLASS_BOOKABLE,
    SCAN_JITTER_RATIO,
    WATCH_POST_OPEN,
    WATCH_PRE_WINDOW_FAR,
    WATCH_PRE_WINDOW_IMMINENT,
    WATCH_PRE_WINDOW_MID,
    WATCH_PRE_WINDOW_NEAR,
)

_LOGGER = logging.getLogger(__name__)

_1MIN = timedelta(minutes=1)
_1H = timedelta(hours=1)
_24H = timedelta(hours=24)


def _select_cadence(offer: Offer) -> timedelta:
    """Return a polling interval based on the offer's booking window."""
    if offer.is_bookable_now:
        base = WATCH_POST_OPEN
    else:
        now = datetime.now(timezone.utc)
        delta = offer.bookable_at - now
        if delta > _24H:
            base = WATCH_PRE_WINDOW_FAR
        elif delta > _1H:
            base = WATCH_PRE_WINDOW_MID
        elif delta > _1MIN:
            base = WATCH_PRE_WINDOW_NEAR
        else:
            base = WATCH_PRE_WINDOW_IMMINENT

    jitter = base * SCAN_JITTER_RATIO * random.random()
    return base + jitter


class WatchedClassCoordinator(DataUpdateCoordinator[WatchedClass]):
    """Polls a watched offer, fires class-bookable events, supports async_book."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BsportClient,
        entry_id: str,
        studio_id: int,
        initial_offer: Offer,
    ):
        self._client = client
        self.entry_id = entry_id
        self._studio_id = studio_id
        self._initial_offer = initial_offer
        self._booked = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_watch_{initial_offer.offer_id}",
            update_interval=_select_cadence(initial_offer),
        )

    def _derive_status(self, offer: Offer) -> WatchStatus:
        if self._booked:
            return "booked"
        if offer.is_bookable_now:
            return "bookable"
        now = datetime.now(timezone.utc)
        if now > offer.end_at:
            return "expired"
        return "awaiting_window"

    async def _async_update_data(self) -> WatchedClass:
        try:
            offers = await self._client.list_upcoming_offers(
                company=self._studio_id,
                date=self._initial_offer.start_at.date().isoformat(),
            )
        except BsportAuthError as err:
            self.hass.bus.async_fire(
                EVENT_AUTH_FAILED,
                {"entry_id": self.entry_id, "email": self._client_email()},
            )
            raise ConfigEntryAuthFailed(str(err)) from err
        except BsportTransientError as err:
            raise UpdateFailed(str(err)) from err

        offer_id = self._initial_offer.offer_id
        matching = next(
            (o for o in offers if o.offer_id == offer_id), None
        )

        if matching is None:
            # Offer gone — treat as expired (or booked elsewhere if flagged)
            status: WatchStatus = "booked" if self._booked else "expired"
            current_offer = self._initial_offer
        else:
            current_offer = matching
            previous = self.data
            if (
                previous is not None
                and previous.status not in ("bookable", "booked")
                and current_offer.is_bookable_now
            ):
                self.hass.bus.async_fire(
                    EVENT_CLASS_BOOKABLE,
                    {
                        "entry_id": self.entry_id,
                        "offer_id": offer_id,
                        "class_name": current_offer.class_name,
                        "category": current_offer.category,
                        "coach": current_offer.coach,
                        "start_at": current_offer.start_at.isoformat(),
                    },
                )
            status = self._derive_status(current_offer)

        self.update_interval = _select_cadence(current_offer)
        return WatchedClass(offer=current_offer, status=status)

    async def async_book(
        self, *, source: Literal["waitlist", "watch", "service"]
    ) -> None:
        """Attempt to book the watched offer."""
        offer = self._initial_offer
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
        self._booked = True
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

    def _client_email(self) -> str:
        return getattr(self._client, "_email", "")  # noqa: SLF001
