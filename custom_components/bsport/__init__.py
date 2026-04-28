"""The bsport integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    BsportAuthError,
    BsportClient,
    BsportTransientError,
)
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    DEFAULT_AUTO_BOOK_LEAD_TIME,
    DOMAIN,
    OPT_AUTO_BOOK_LEAD_TIME,
    OPT_WATCHED_OFFER_IDS,
    PLATFORMS,
)
from .coordinator_overview import AccountOverviewCoordinator
from .coordinator_waitlist import WaitlistBatchCache, WaitlistEntryCoordinator
from .coordinator_watch import WatchedClassCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class BsportRuntimeData:
    client: BsportClient
    overview: AccountOverviewCoordinator
    waitlist_cache: WaitlistBatchCache
    waitlists: dict[int, WaitlistEntryCoordinator] = field(default_factory=dict)
    watches: dict[int, WatchedClassCoordinator] = field(default_factory=dict)
    # Platform async_add_entities callbacks, captured during platform setup so
    # the reconciler can create entities for coordinators spawned mid-life.
    add_sensor_entities: AddEntitiesCallback | None = None
    add_button_entities: AddEntitiesCallback | None = None
    add_switch_entities: AddEntitiesCallback | None = None


type BsportConfigEntry = ConfigEntry[BsportRuntimeData]


def _install_bundled_blueprints(hass_config_dir: str) -> list[str]:
    """Copy blueprints shipped with the integration into the user's config dir.

    Installed blueprints live at `<config>/blueprints/automation/bsport/*.yaml`
    — the directory HA's blueprint system discovers from. We copy the bundled
    files from `custom_components/bsport/blueprints/automation/bsport/` only
    when the target file is missing, so user-local edits to an already-copied
    blueprint are never clobbered.

    Returns the list of filenames that were actually written (empty if all
    targets already existed). Pure filesystem work — safe to offload to an
    executor.
    """
    source_dir = Path(__file__).parent / "blueprints" / "automation" / DOMAIN
    target_dir = Path(hass_config_dir) / "blueprints" / "automation" / DOMAIN
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    if not source_dir.is_dir():
        return written
    for src in source_dir.glob("*.yaml"):
        dst = target_dir / src.name
        if dst.exists():
            continue
        dst.write_bytes(src.read_bytes())
        written.append(src.name)
    return written


def _remove_pristine_bundled_blueprints(hass_config_dir: str) -> list[str]:
    """Remove auto-installed blueprints that the user hasn't modified.

    A blueprint file is considered *pristine* if its bytes match exactly the
    version shipped in the integration. User-modified files are left alone so
    a removal never clobbers customisation work. If the `bsport/` subdir
    becomes empty it's removed too.

    Only called from `async_remove_entry`, and only when the LAST bsport
    config entry is being removed (caller's responsibility).
    """
    source_dir = Path(__file__).parent / "blueprints" / "automation" / DOMAIN
    target_dir = Path(hass_config_dir) / "blueprints" / "automation" / DOMAIN
    if not target_dir.is_dir():
        return []

    removed: list[str] = []
    if source_dir.is_dir():
        for src in source_dir.glob("*.yaml"):
            dst = target_dir / src.name
            try:
                if dst.exists() and dst.read_bytes() == src.read_bytes():
                    dst.unlink()
                    removed.append(src.name)
            except OSError:
                # Bad permissions, race with the user editing, etc. Leave the
                # file in place rather than fail the uninstall.
                continue

    # If the domain's blueprint directory is now empty, clean it up too.
    # Don't touch `<config>/blueprints/automation/` itself — other integrations
    # may be using sibling directories under there.
    try:
        target_dir.rmdir()
    except OSError:
        pass
    return removed


async def async_setup_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> bool:
    """Set up bsport from a config entry."""
    from .services import async_register_services
    async_register_services(hass)

    # Install any bundled automation blueprints (idempotent — only writes
    # files that don't already exist). Does filesystem work, so it runs in
    # the executor to keep the event loop unblocked.
    try:
        installed = await hass.async_add_executor_job(
            _install_bundled_blueprints, hass.config.config_dir
        )
        if installed:
            _LOGGER.info(
                "installed bundled bsport blueprints: %s", ", ".join(installed)
            )
    except OSError as err:
        # Non-fatal: the integration works without the blueprint, it's just
        # a nicer automation UX.
        _LOGGER.warning("could not install bundled blueprints: %s", err)

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

    runtime = BsportRuntimeData(
        client=client,
        overview=overview,
        waitlist_cache=WaitlistBatchCache(client),
    )
    entry.runtime_data = runtime

    await _reconcile_child_coordinators(hass, entry)

    # Clean up orphan devices left by earlier versions of the integration
    # that retired coordinators without removing their device entries.
    _sweep_orphaned_child_devices(
        hass,
        entry,
        live_waitlists=set(runtime.waitlists),
        live_watches=set(runtime.watches),
    )

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


async def async_remove_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Called when the user removes a config entry.

    If this is the *last* bsport entry being removed, clean up any pristine
    auto-installed blueprints. Preserves user-modified blueprints and never
    touches anything when other bsport entries remain.
    """
    # Are there still other bsport entries? This hook fires AFTER the current
    # entry has already been removed from the registry, so we can just ask.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if remaining:
        return

    try:
        removed = await hass.async_add_executor_job(
            _remove_pristine_bundled_blueprints, hass.config.config_dir
        )
        if removed:
            _LOGGER.info(
                "removed bundled bsport blueprints on uninstall: %s",
                ", ".join(removed),
            )
    except OSError as err:
        _LOGGER.warning("could not clean up bundled blueprints: %s", err)


async def _async_reload_entry(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Trigger a reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_child_device(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    kind: str,
    offer_id: int,
) -> None:
    """Remove a per-waitlist / per-watch device and its entities from HA.

    The identifier scheme matches `_waitlist_device` / `_watch_device` in
    sensor.py. Cascades to entities automatically via the registry.
    """
    registry = dr.async_get(hass)
    identifier = f"{entry.entry_id}_{kind}_{offer_id}"
    device = registry.async_get_device(identifiers={(DOMAIN, identifier)})
    if device is not None:
        registry.async_remove_device(device.id)


def _sweep_orphaned_child_devices(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    *,
    live_waitlists: set[int],
    live_watches: set[int],
) -> None:
    """Remove any `waitlist_<id>` / `watch_<id>` devices that no longer
    correspond to a live coordinator — catches orphans created before the
    reconcile loop learned to clean up after itself."""
    registry = dr.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, ident in device.identifiers:
            if domain != DOMAIN or not ident.startswith(prefix):
                continue
            rest = ident[len(prefix):]
            if rest.startswith("waitlist_"):
                try:
                    oid = int(rest[len("waitlist_"):])
                except ValueError:
                    continue
                if oid not in live_waitlists:
                    registry.async_remove_device(device.id)
            elif rest.startswith("watch_"):
                try:
                    oid = int(rest[len("watch_"):])
                except ValueError:
                    continue
                if oid not in live_watches:
                    registry.async_remove_device(device.id)
            # Hub device (no suffix) is always kept while the entry exists.


async def _reconcile_child_coordinators(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Spawn / retire per-waitlist and per-watch coordinators."""
    runtime = entry.runtime_data
    overview = runtime.overview.data
    if overview is None:
        return

    # Lead time is sourced from options; the entry-reload listener
    # reconstructs coordinators when the user changes it.
    lead_time_seconds = entry.options.get(
        OPT_AUTO_BOOK_LEAD_TIME,
        int(DEFAULT_AUTO_BOOK_LEAD_TIME.total_seconds()),
    )
    auto_book_lead_time = timedelta(seconds=int(lead_time_seconds))

    # Waitlist coordinators
    live_ids = {w.offer.offer_id for w in overview.waitlists}
    for dead_id in list(runtime.waitlists):
        if dead_id not in live_ids:
            await runtime.waitlists.pop(dead_id).async_shutdown()
            _remove_child_device(hass, entry, "waitlist", dead_id)
    for entry_obj in overview.waitlists:
        oid = entry_obj.offer.offer_id
        if oid not in runtime.waitlists:
            coord = WaitlistEntryCoordinator(
                hass, runtime.client, entry_id=entry.entry_id,
                initial=entry_obj,
                batch_cache=runtime.waitlist_cache,
                auto_book_lead_time=auto_book_lead_time,
            )
            # async_refresh (not async_config_entry_first_refresh) because
            # reconcile runs both during SETUP_IN_PROGRESS (initial load)
            # and later when the overview listener fires with state=LOADED;
            # the latter rejects async_config_entry_first_refresh. Fetch
            # failure falls back to _initial data in sensors/buttons.
            await coord.async_refresh()
            runtime.waitlists[oid] = coord
            _spawn_waitlist_entities(runtime, entry, coord, entry_obj.offer.class_name)

    # Watch coordinators
    desired_watches = set(entry.options.get(OPT_WATCHED_OFFER_IDS, []))
    for dead_id in list(runtime.watches):
        if dead_id not in desired_watches:
            await runtime.watches.pop(dead_id).async_shutdown()
            _remove_child_device(hass, entry, "watch", dead_id)
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
        await coord.async_refresh()
        runtime.watches[offer_id] = coord
        _spawn_watch_entities(runtime, entry, coord, offer_id, offer.class_name)


def _spawn_waitlist_entities(
    runtime: BsportRuntimeData,
    entry: BsportConfigEntry,
    coord: WaitlistEntryCoordinator,
    class_name: str,
) -> None:
    """Add sensor/button/switch entities for a waitlist coord spawned mid-life.

    No-op during SETUP_IN_PROGRESS: platform setup hasn't run yet, so the
    callbacks are None; the platform will pick the coord up by iterating
    runtime.waitlists when it does run. Late imports avoid a circular dep
    (sensor/button/switch import from this module).
    """
    offer_id = coord._initial.offer.offer_id  # noqa: SLF001
    if runtime.add_sensor_entities is not None:
        from .sensor import WaitlistPositionSensor, WaitlistStatusSensor
        runtime.add_sensor_entities([
            WaitlistStatusSensor(coord, entry, offer_id, class_name),
            WaitlistPositionSensor(coord, entry, offer_id, class_name),
        ])
    if runtime.add_button_entities is not None:
        from .button import WaitlistBookButton, WaitlistDiscardButton
        runtime.add_button_entities([
            WaitlistBookButton(coord, entry),
            WaitlistDiscardButton(coord, entry),
        ])
    if runtime.add_switch_entities is not None:
        from .switch import WaitlistAutoBookSwitch
        runtime.add_switch_entities([
            WaitlistAutoBookSwitch(coord, entry),
        ])


def _spawn_watch_entities(
    runtime: BsportRuntimeData,
    entry: BsportConfigEntry,
    coord: WatchedClassCoordinator,
    offer_id: int,
    class_name: str,
) -> None:
    """Add sensor/button entities for a watch coord spawned mid-life."""
    if runtime.add_sensor_entities is not None:
        from .sensor import WatchOpensAtSensor, WatchStatusSensor
        runtime.add_sensor_entities([
            WatchStatusSensor(coord, entry, offer_id, class_name),
            WatchOpensAtSensor(coord, entry, offer_id, class_name),
        ])
    if runtime.add_button_entities is not None:
        from .button import WatchBookButton
        runtime.add_button_entities([WatchBookButton(coord, entry)])
