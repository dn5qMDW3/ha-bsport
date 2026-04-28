"""Switch platform for bsport — auto-book toggles."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BsportConfigEntry
from .const import DOMAIN
from .coordinator_waitlist import WaitlistEntryCoordinator
from .sensor import _waitlist_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bsport switches from a config entry."""
    runtime = entry.runtime_data
    entities: list[SwitchEntity] = []
    for coord in runtime.waitlists.values():
        entities.append(WaitlistAutoBookSwitch(coord, entry))
    async_add_entities(entities)
    # Expose so the reconciler can spawn per-child switches mid-life.
    runtime.add_switch_entities = async_add_entities


class WaitlistAutoBookSwitch(
    CoordinatorEntity[WaitlistEntryCoordinator], SwitchEntity, RestoreEntity,
):
    """Per-waitlist auto-book toggle.

    The switch is a thin UI mirror of `coord._auto_book_enabled`. The
    coordinator owns the gate logic (status convertible, lead time, lock) and
    the actual book call; this entity only sets the flag and persists state
    via RestoreEntity so a restart preserves the user's choice.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_autobook"

    def __init__(
        self, coord: WaitlistEntryCoordinator, entry: BsportConfigEntry,
    ) -> None:
        super().__init__(coord)
        offer = (
            coord.data.offer if coord.data else coord._initial.offer  # noqa: SLF001
        )
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_waitlist_autobook_{offer.offer_id}"
        )
        self._attr_device_info = _waitlist_device(
            entry, offer.offer_id, offer.class_name, offer.start_at,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator._auto_book_enabled  # noqa: SLF001

    @property
    def entity_picture(self) -> str | None:
        data = self.coordinator.data
        return data.offer.cover_url if data else None

    async def async_added_to_hass(self) -> None:
        """Restore state on startup. If restored ON, apply to coordinator
        and trigger an immediate auto-book check for the
        already-convertible-at-boot edge case."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            self.coordinator._auto_book_enabled = True  # noqa: SLF001
            if self.coordinator.data is not None:
                await self.coordinator.async_maybe_auto_book()

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator._auto_book_enabled = True  # noqa: SLF001
        self.async_write_ha_state()
        await self.coordinator.async_maybe_auto_book()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator._auto_book_enabled = False  # noqa: SLF001
        self.async_write_ha_state()
