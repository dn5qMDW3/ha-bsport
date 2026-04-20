"""The bsport integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    BsportAuthError,
    BsportClient,
    BsportTransientError,
)
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    OPT_WATCHED_OFFER_IDS,
    PLATFORMS,
)
from .coordinator_overview import AccountOverviewCoordinator
from .coordinator_waitlist import WaitlistEntryCoordinator
from .coordinator_watch import WatchedClassCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class BsportRuntimeData:
    client: BsportClient
    overview: AccountOverviewCoordinator
    waitlists: dict[int, WaitlistEntryCoordinator] = field(default_factory=dict)
    watches: dict[int, WatchedClassCoordinator] = field(default_factory=dict)


type BsportConfigEntry = ConfigEntry[BsportRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> bool:
    """Set up bsport from a config entry."""
    from .services import async_register_services
    async_register_services(hass)

    session = async_get_clientsession(hass)
    client = BsportClient(
        session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    try:
        await client.authenticate()
    except BsportAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BsportTransientError as err:
        _LOGGER.warning("bsport transient error on setup: %s", err)
        raise

    overview = AccountOverviewCoordinator(
        hass, client, entry_id=entry.entry_id
    )
    await overview.async_config_entry_first_refresh()

    runtime = BsportRuntimeData(client=client, overview=overview)
    entry.runtime_data = runtime

    await _reconcile_child_coordinators(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(
        overview.async_add_listener(
            lambda: hass.async_create_task(
                _reconcile_child_coordinators(hass, entry)
            )
        )
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> bool:
    """Unload a config entry."""
    runtime = entry.runtime_data
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    for coord in (
        list(runtime.waitlists.values()) + list(runtime.watches.values())
    ):
        await coord.async_shutdown()
    return ok


async def _async_reload_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Trigger a reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _reconcile_child_coordinators(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Spawn / retire per-waitlist and per-watch coordinators."""
    runtime = entry.runtime_data
    overview = runtime.overview.data
    if overview is None:
        return

    # Waitlist coordinators
    live_ids = {w.offer.offer_id for w in overview.waitlists}
    for dead_id in list(runtime.waitlists):
        if dead_id not in live_ids:
            await runtime.waitlists.pop(dead_id).async_shutdown()
    for entry_obj in overview.waitlists:
        oid = entry_obj.offer.offer_id
        if oid not in runtime.waitlists:
            coord = WaitlistEntryCoordinator(
                hass, runtime.client, entry_id=entry.entry_id,
                initial=entry_obj,
            )
            await coord.async_config_entry_first_refresh()
            runtime.waitlists[oid] = coord

    # Watch coordinators
    desired_watches = set(entry.options.get(OPT_WATCHED_OFFER_IDS, []))
    for dead_id in list(runtime.watches):
        if dead_id not in desired_watches:
            await runtime.watches.pop(dead_id).async_shutdown()
    for offer_id in desired_watches:
        if offer_id in runtime.watches:
            continue
        # Fetch the offer to get its start date and build the Offer dataclass.
        # We scan an upcoming window broadly then filter.
        try:
            offers = await runtime.client.list_upcoming_offers(
                company=entry.data[CONF_STUDIO_ID],
            )
        except BsportTransientError as err:
            _LOGGER.warning("cannot initialise watch %s: %s", offer_id, err)
            continue
        offer = next((o for o in offers if o.offer_id == offer_id), None)
        if offer is None:
            _LOGGER.warning("watched offer %s not found in schedule", offer_id)
            continue
        coord = WatchedClassCoordinator(
            hass, runtime.client, entry_id=entry.entry_id,
            studio_id=entry.data[CONF_STUDIO_ID], initial_offer=offer,
        )
        await coord.async_config_entry_first_refresh()
        runtime.watches[offer_id] = coord
