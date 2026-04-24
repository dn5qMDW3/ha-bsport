"""Sensor platform for bsport."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import BsportConfigEntry
from .const import CONF_STUDIO_COVER, DOMAIN
from .coordinator_overview import AccountOverviewCoordinator
from .coordinator_waitlist import WaitlistEntryCoordinator
from .coordinator_watch import WatchedClassCoordinator


def _studio_cover(entry: BsportConfigEntry) -> str | None:
    """Public URL to the studio's branding image (set during config flow).

    Returns None for entries created before CONF_STUDIO_COVER was added, so
    upgrades don't break — HA just falls back to the device_class icon.
    """
    cover = entry.data.get(CONF_STUDIO_COVER)
    return cover if isinstance(cover, str) and cover else None


# ---------------------------------------------------------------------------
# Device helpers (re-exported for button.py and calendar.py)
# ---------------------------------------------------------------------------

def _hub_device(entry: BsportConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="bsport",
        model="Account",
    )


def _format_class_when(start_at: datetime) -> str:
    """Short, human-readable local time for a class start — e.g. 'Sat 26 Apr 18:00'.

    Rendered from HA's local timezone so the name matches what the user sees
    in the bsport app. strftime's %a/%b honor the process locale, which HA
    typically leaves as English; users on another system locale get their
    own short names. Intended as a disambiguator in device names when the
    same class repeats on different days.
    """
    return dt_util.as_local(start_at).strftime("%a %d %b %H:%M")


def _waitlist_device(
    entry: BsportConfigEntry,
    offer_id: int,
    class_name: str,
    start_at: datetime,
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_waitlist_{offer_id}")},
        via_device=(DOMAIN, entry.entry_id),
        name=f"Waitlist · {class_name} · {_format_class_when(start_at)}",
        manufacturer="bsport",
        model="Waitlist entry",
    )


def _watch_device(
    entry: BsportConfigEntry,
    offer_id: int,
    class_name: str,
    start_at: datetime,
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_watch_{offer_id}")},
        via_device=(DOMAIN, entry.entry_id),
        name=f"Watch · {class_name} · {_format_class_when(start_at)}",
        manufacturer="bsport",
        model="Watched class",
    )


# ---------------------------------------------------------------------------
# Hub sensors
# ---------------------------------------------------------------------------

class NextBookingSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "next_booking"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_next_booking"
        self._attr_device_info = _hub_device(entry)
        # Hub-device showcase entity — decorated with the studio's logo.
        self._attr_entity_picture = _studio_cover(entry)

    @property
    def native_value(self) -> datetime | None:
        overview = self.coordinator.data
        if overview is None:
            return None
        confirmed = [
            b.offer.start_at
            for b in overview.bookings
            if b.status == "confirmed"
        ]
        return min(confirmed) if confirmed else None


class UpcomingBookingCountSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "upcoming_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_upcoming_count"
        self._attr_device_info = _hub_device(entry)

    @property
    def native_value(self) -> int | None:
        overview = self.coordinator.data
        if overview is None:
            return None
        return sum(1 for b in overview.bookings if b.status == "confirmed")


class PassClassesRemainingSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "pass_classes_remaining"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_pass_classes_remaining"
        self._attr_device_info = _hub_device(entry)

    @property
    def native_value(self) -> int | None:
        overview = self.coordinator.data
        if overview is None or overview.active_pass is None:
            return None
        return overview.active_pass.classes_remaining


class PassExpiresSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "pass_expires"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_pass_expires"
        self._attr_device_info = _hub_device(entry)

    @property
    def native_value(self) -> datetime | None:
        overview = self.coordinator.data
        if overview is None or overview.active_pass is None:
            return None
        return overview.active_pass.expires_at


class MembershipStatusSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "membership_status"

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_membership_status"
        self._attr_device_info = _hub_device(entry)

    @property
    def native_value(self) -> str | None:
        overview = self.coordinator.data
        if overview is None or overview.membership is None:
            return None
        return overview.membership.status


class MembershipRenewalSensor(
    CoordinatorEntity[AccountOverviewCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "membership_renewal"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coord: AccountOverviewCoordinator, entry: BsportConfigEntry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_membership_renewal"
        self._attr_device_info = _hub_device(entry)

    @property
    def native_value(self) -> datetime | None:
        overview = self.coordinator.data
        if overview is None or overview.membership is None:
            return None
        return overview.membership.next_renewal_at


# ---------------------------------------------------------------------------
# Waitlist sensors
# ---------------------------------------------------------------------------

class WaitlistStatusSensor(
    CoordinatorEntity[WaitlistEntryCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_status"

    def __init__(
        self,
        coord: WaitlistEntryCoordinator,
        entry: BsportConfigEntry,
        offer_id: int,
        class_name: str,
    ) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_waitlist_status_{offer_id}"
        self._attr_device_info = _waitlist_device(
            entry, offer_id, class_name, coord._initial.offer.start_at,  # noqa: SLF001
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status

    @property
    def entity_picture(self) -> str | None:
        """Class cover image from the current offer (falls back to None)."""
        data = self.coordinator.data
        return data.offer.cover_url if data else None


class WaitlistPositionSensor(
    CoordinatorEntity[WaitlistEntryCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_position"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coord: WaitlistEntryCoordinator,
        entry: BsportConfigEntry,
        offer_id: int,
        class_name: str,
    ) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_waitlist_position_{offer_id}"
        self._attr_device_info = _waitlist_device(
            entry, offer_id, class_name, coord._initial.offer.start_at,  # noqa: SLF001
        )

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.position

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Expose queue size and dynamic-type flag alongside the position."""
        data = self.coordinator.data
        if data is None:
            return None
        attrs: dict[str, int] = {}
        if data.waiting_list_size is not None:
            # Number of other people waiting on the same offer (user not
            # counted). Useful for "someone's in front of me" automations.
            attrs["others_in_queue"] = data.waiting_list_size
        if data.dynamic is not None:
            # 1 = dynamic/priority-based queue, 0 = strict FIFO.
            attrs["queue_type"] = "dynamic" if data.dynamic == 1 else "fifo"
        return attrs or None


# ---------------------------------------------------------------------------
# Watch sensors
# ---------------------------------------------------------------------------

class WatchStatusSensor(
    CoordinatorEntity[WatchedClassCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "watch_status"

    def __init__(
        self,
        coord: WatchedClassCoordinator,
        entry: BsportConfigEntry,
        offer_id: int,
        class_name: str,
    ) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_watch_status_{offer_id}"
        self._attr_device_info = _watch_device(
            entry, offer_id, class_name, coord._initial_offer.start_at,  # noqa: SLF001
        )

    @property
    def entity_picture(self) -> str | None:
        """Class cover image from the watched offer (None when the flat
        /book/v1/offer/ schedule doesn't carry covers)."""
        data = self.coordinator.data
        return data.offer.cover_url if data else None

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status


class WatchOpensAtSensor(
    CoordinatorEntity[WatchedClassCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "watch_opens_at"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        coord: WatchedClassCoordinator,
        entry: BsportConfigEntry,
        offer_id: int,
        class_name: str,
    ) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_watch_opens_at_{offer_id}"
        self._attr_device_info = _watch_device(
            entry, offer_id, class_name, coord._initial_offer.start_at,  # noqa: SLF001
        )

    @property
    def native_value(self) -> datetime | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.offer.bookable_at


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bsport sensors from a config entry."""
    runtime = entry.runtime_data
    coord = runtime.overview

    entities: list[SensorEntity] = [
        NextBookingSensor(coord, entry),
        UpcomingBookingCountSensor(coord, entry),
        PassClassesRemainingSensor(coord, entry),
        PassExpiresSensor(coord, entry),
        MembershipStatusSensor(coord, entry),
        MembershipRenewalSensor(coord, entry),
    ]

    for offer_id, wl_coord in runtime.waitlists.items():
        initial = wl_coord._initial  # noqa: SLF001
        class_name = initial.offer.class_name
        entities.append(WaitlistStatusSensor(wl_coord, entry, offer_id, class_name))
        entities.append(WaitlistPositionSensor(wl_coord, entry, offer_id, class_name))

    for offer_id, w_coord in runtime.watches.items():
        offer = w_coord._initial_offer  # noqa: SLF001
        class_name = offer.class_name
        entities.append(WatchStatusSensor(w_coord, entry, offer_id, class_name))
        entities.append(WatchOpensAtSensor(w_coord, entry, offer_id, class_name))

    async_add_entities(entities)
    # Expose the callback so the reconciler can spawn per-child sensors when
    # new waitlist/watch coordinators appear after initial setup.
    runtime.add_sensor_entities = async_add_entities
