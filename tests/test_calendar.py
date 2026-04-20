"""Calendar entity tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview, Booking, Offer,
)
from custom_components.bsport.const import (
    CONF_EMAIL, CONF_PASSWORD, CONF_STUDIO_ID, CONF_STUDIO_NAME,
    DOMAIN, OPT_WATCHED_OFFER_IDS,
)


def _offer(hours_ahead: int = 2) -> Offer:
    start = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return Offer(
        offer_id=1, class_name="Yoga", category="Yoga", coach="Marc",
        start_at=start, end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False, is_waitlist_only=False,
    )


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "user@example.com", CONF_PASSWORD: "pw",
            "bsport_token": "tok", "bsport_user_id": 9999999,
            CONF_STUDIO_ID: 538, CONF_STUDIO_NAME: "Chimosa",
        },
        options={OPT_WATCHED_OFFER_IDS: []},
        unique_id="9999999",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_calendar_entity_exists_and_returns_events(hass: HomeAssistant):
    offer = _offer()
    overview = AccountOverview(
        waitlists=(),
        bookings=(Booking(booking_id=1, offer=offer, status="confirmed"),),
        active_pass=None,
        membership=None,
    )
    entry = _entry(hass)

    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_account_overview",
        new=AsyncMock(return_value=overview),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The calendar entity should exist with the expected unique_id
    ent_reg = er.async_get(hass)
    expected_uid = f"{DOMAIN}_{entry.entry_id}_bookings_calendar"
    cal_entries = [e for e in ent_reg.entities.values() if e.unique_id == expected_uid]
    assert cal_entries, (
        f"calendar unique_id {expected_uid!r} not found; "
        f"registered: {[e.unique_id for e in ent_reg.entities.values()]}"
    )

    # async_get_events should return the confirmed booking
    from custom_components.bsport.calendar import BookingsCalendar
    runtime = entry.runtime_data
    cal = BookingsCalendar(runtime.overview, entry)
    now = datetime.now(timezone.utc)
    events = await cal.async_get_events(hass, now, now + timedelta(days=7))
    assert len(events) == 1
    assert events[0].summary == "Yoga"
    assert events[0].description == "Yoga"
    assert events[0].location == "Marc"
