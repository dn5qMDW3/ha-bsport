"""Sensor entity tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview, Booking, Offer, WaitlistEntry,
)
from custom_components.bsport.const import (
    CONF_EMAIL, CONF_PASSWORD, CONF_STUDIO_ID, CONF_STUDIO_NAME,
    DOMAIN, OPT_WATCHED_OFFER_IDS,
)


def _offer() -> Offer:
    start = datetime.now(timezone.utc) + timedelta(hours=2)
    return Offer(
        offer_id=1, class_name="Pilates", category="Pilates", coach="Léa",
        start_at=start, end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False, is_waitlist_only=False,
    )


def _entry(hass: HomeAssistant, overview: AccountOverview) -> MockConfigEntry:
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
async def test_hub_sensors_appear_and_report_values(hass: HomeAssistant):
    offer = _offer()
    overview = AccountOverview(
        waitlists=(), bookings=(Booking(booking_id=1, offer=offer, status="confirmed"),),
        active_pass=None, membership=None,
    )
    entry = _entry(hass, overview)
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_account_overview",
        new=AsyncMock(return_value=overview),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    unique_ids = {e.unique_id for e in ent_reg.entities.values()}
    assert f"{DOMAIN}_{entry.entry_id}_next_booking" in unique_ids, (
        f"unique_ids: {unique_ids}"
    )
    assert f"{DOMAIN}_{entry.entry_id}_upcoming_count" in unique_ids


@pytest.mark.asyncio
async def test_waitlist_sensors_created_per_entry(hass: HomeAssistant):
    offer = _offer()
    waitlist = WaitlistEntry(
        entry_id=6521868, offer=offer, status="convertible", position=None,
    )
    overview = AccountOverview(
        waitlists=(waitlist,), bookings=(), active_pass=None, membership=None,
    )
    entry = _entry(hass, overview)
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_account_overview",
        new=AsyncMock(return_value=overview),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_waitlist_entry",
        new=AsyncMock(return_value=waitlist),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    unique_ids = {e.unique_id for e in ent_reg.entities.values()}
    offer_id = offer.offer_id
    assert f"{DOMAIN}_{entry.entry_id}_waitlist_status_{offer_id}" in unique_ids, (
        f"unique_ids: {unique_ids}"
    )
    assert f"{DOMAIN}_{entry.entry_id}_waitlist_position_{offer_id}" in unique_ids
