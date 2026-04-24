"""Button platform for bsport — book actions."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BsportConfigEntry
from .const import DOMAIN
from .coordinator_waitlist import WaitlistEntryCoordinator
from .coordinator_watch import WatchedClassCoordinator
from .sensor import _waitlist_device, _watch_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[ButtonEntity] = []
    for coord in runtime.waitlists.values():
        entities.append(WaitlistBookButton(coord, entry))
        entities.append(WaitlistDiscardButton(coord, entry))
    for coord in runtime.watches.values():
        entities.append(WatchBookButton(coord, entry))
    async_add_entities(entities)
    # Expose the callback so the reconciler can spawn per-child buttons when
    # new waitlist/watch coordinators appear after initial setup.
    runtime.add_button_entities = async_add_entities


class WaitlistBookButton(
    CoordinatorEntity[WaitlistEntryCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_book"

    def __init__(self, coord: WaitlistEntryCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        offer = (coord.data.offer if coord.data else coord._initial.offer)  # noqa: SLF001
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_waitlist_book_{offer.offer_id}"
        )
        self._attr_device_info = _waitlist_device(
            entry, offer.offer_id, offer.class_name, offer.start_at,
        )

    @property
    def entity_picture(self) -> str | None:
        """Track the class cover from the current coordinator payload."""
        data = self.coordinator.data
        return data.offer.cover_url if data else None

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and data.status == "convertible"
        )

    async def async_press(self) -> None:
        await self.coordinator.async_book(source="waitlist")


class WaitlistDiscardButton(
    CoordinatorEntity[WaitlistEntryCoordinator], ButtonEntity
):
    """Leave the waitlist for this class."""

    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_discard"

    def __init__(self, coord: WaitlistEntryCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        offer = (coord.data.offer if coord.data else coord._initial.offer)  # noqa: SLF001
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_waitlist_discard_{offer.offer_id}"
        )
        self._attr_device_info = _waitlist_device(
            entry, offer.offer_id, offer.class_name, offer.start_at,
        )

    async def async_press(self) -> None:
        await self.coordinator.async_discard()
        # Refresh the overview so reconcile retires this coordinator and
        # its device/entities disappear without a full integration reload.
        entry = self.coordinator.hass.config_entries.async_get_entry(
            self.coordinator.entry_id
        )
        if entry is not None and hasattr(entry, "runtime_data"):
            await entry.runtime_data.overview.async_request_refresh()


class WatchBookButton(
    CoordinatorEntity[WatchedClassCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "watch_book"

    def __init__(self, coord: WatchedClassCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        offer = (coord.data.offer if coord.data else coord._initial_offer)  # noqa: SLF001
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_watch_book_{offer.offer_id}"
        )
        self._attr_device_info = _watch_device(
            entry, offer.offer_id, offer.class_name, offer.start_at,
        )

    @property
    def entity_picture(self) -> str | None:
        data = self.coordinator.data
        return data.offer.cover_url if data else None

    async def async_press(self) -> None:
        await self.coordinator.async_book(source="watch")
