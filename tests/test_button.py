"""Button entity tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview, Offer, WaitlistEntry,
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
async def test_waitlist_book_button_press_calls_async_book(hass: HomeAssistant):
    offer = _offer()
    waitlist = WaitlistEntry(
        entry_id=6521868, offer=offer, status="convertible", position=None,
    )
    overview = AccountOverview(
        waitlists=(waitlist,), bookings=(), active_pass=None, membership=None,
    )
    entry = _entry(hass)
    book_mock = AsyncMock(return_value=None)

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

    # Verify button entity exists via registry
    ent_reg = er.async_get(hass)
    expected_uid = f"{DOMAIN}_{entry.entry_id}_waitlist_book_{offer.offer_id}"
    button_entries = [e for e in ent_reg.entities.values() if e.unique_id == expected_uid]
    assert button_entries, (
        f"button unique_id {expected_uid!r} not found; "
        f"registered: {[e.unique_id for e in ent_reg.entities.values()]}"
    )

    # Patch async_book on the coordinator and press the button
    runtime = entry.runtime_data
    wl_coord = list(runtime.waitlists.values())[0]
    wl_coord.async_book = book_mock

    entity_id = button_entries[0].entity_id
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    book_mock.assert_called_once_with(source="waitlist")
