"""Service registration for bsport."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import BsportBookError, BsportError
from .const import DOMAIN, OPT_WATCHED_OFFER_IDS

_BOOK_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("offer_id"): vol.Coerce(int),
    }
)

_WATCH_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("offer_id"): vol.Coerce(int),
    }
)


def _resolve_entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError(f"unknown bsport config entry: {entry_id}")
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        raise HomeAssistantError(f"bsport entry {entry_id} not loaded")
    return entry, runtime


async def _book_offer(call: ServiceCall) -> None:
    entry, runtime = _resolve_entry(call.hass, call.data["entry_id"])
    try:
        await runtime.client.book_offer(int(call.data["offer_id"]))
    except BsportBookError as err:
        raise HomeAssistantError(f"bsport book failed: {err.reason}") from err
    except BsportError as err:
        raise HomeAssistantError(str(err)) from err
    await runtime.overview.async_request_refresh()


async def _cancel_booking(call: ServiceCall) -> None:
    entry, runtime = _resolve_entry(call.hass, call.data["entry_id"])
    try:
        await runtime.client.cancel_booking(int(call.data["offer_id"]))
    except BsportError as err:
        raise HomeAssistantError(str(err)) from err
    await runtime.overview.async_request_refresh()


async def _watch_class(call: ServiceCall) -> None:
    entry, _runtime = _resolve_entry(call.hass, call.data["entry_id"])
    current = list(entry.options.get(OPT_WATCHED_OFFER_IDS, []))
    oid = int(call.data["offer_id"])
    if oid not in current:
        current.append(oid)
    call.hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_WATCHED_OFFER_IDS: current}
    )


async def _unwatch_class(call: ServiceCall) -> None:
    entry, _runtime = _resolve_entry(call.hass, call.data["entry_id"])
    oid = int(call.data["offer_id"])
    current = [
        x for x in entry.options.get(OPT_WATCHED_OFFER_IDS, []) if x != oid
    ]
    call.hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_WATCHED_OFFER_IDS: current}
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register all bsport services. Idempotent."""
    if hass.services.has_service(DOMAIN, "book_offer"):
        return
    hass.services.async_register(
        DOMAIN, "book_offer", _book_offer, schema=_BOOK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "cancel_booking", _cancel_booking, schema=_BOOK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "watch_class", _watch_class, schema=_WATCH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "unwatch_class", _unwatch_class, schema=_WATCH_SCHEMA
    )
