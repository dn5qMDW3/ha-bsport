"""Calendar platform for bsport — upcoming bookings."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BsportConfigEntry
from .const import DOMAIN
from .coordinator_overview import AccountOverviewCoordinator
from .sensor import _hub_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities([BookingsCalendar(runtime.overview, entry)])


class BookingsCalendar(
    CoordinatorEntity[AccountOverviewCoordinator], CalendarEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "bookings"

    def __init__(
        self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry
    ) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_bookings_calendar"
        self._attr_device_info = _hub_device(entry)

    def _events(self) -> list[CalendarEvent]:
        overview = self.coordinator.data
        if overview is None:
            return []
        events = []
        for booking in overview.bookings:
            if booking.status != "confirmed":
                continue
            offer = booking.offer
            events.append(
                CalendarEvent(
                    start=offer.start_at,
                    end=offer.end_at,
                    summary=offer.category or offer.class_name,
                    description=offer.class_name,
                    location=offer.coach or "",
                )
            )
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = datetime.now(timezone.utc)
        future = [e for e in self._events() if e.start > now]
        return min(future, key=lambda e: e.start) if future else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self._events()
            if e.start < end_date and e.end > start_date
        ]
