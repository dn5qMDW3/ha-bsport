"""Switch platform for bsport — auto-book toggle."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BsportConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bsport switch entities."""
    runtime = entry.runtime_data
    runtime.add_switch_entities = async_add_entities
