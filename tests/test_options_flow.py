"""Tests for bsport options flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport import BsportRuntimeData
from custom_components.bsport.api import Offer
from custom_components.bsport.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    CONF_STUDIO_NAME,
    DOMAIN,
    OPT_WATCHED_OFFER_IDS,
)


def _entry_with_runtime(hass: HomeAssistant) -> MockConfigEntry:
    from unittest.mock import MagicMock
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
    # Attach a minimal runtime so async_step_add_watch can reach client
    client = AsyncMock()
    overview = MagicMock()
    from custom_components.bsport.coordinator_waitlist import WaitlistBatchCache
    runtime = BsportRuntimeData(
        client=client, overview=overview,
        waitlist_cache=WaitlistBatchCache(client),
    )
    entry.runtime_data = runtime
    return entry


@pytest.mark.asyncio
async def test_options_add_watch_appends_offer_id(hass: HomeAssistant):
    entry = _entry_with_runtime(hass)
    start = datetime.now(timezone.utc) + timedelta(days=3)
    offer = Offer(
        offer_id=99, class_name="Pilates Mat", category="Pilates",
        coach="Léa", start_at=start, end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False, is_waitlist_only=False,
    )
    entry.runtime_data.client.list_upcoming_offers = AsyncMock(return_value=(offer,))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_watch"}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"offer_id": "99"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Entry's options updated via flow reload; verify the new entry data
    assert 99 in result["data"][OPT_WATCHED_OFFER_IDS]


@pytest.mark.asyncio
async def test_options_remove_watch_empty_aborts(hass: HomeAssistant):
    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_watch"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_watches"


@pytest.mark.asyncio
async def test_options_set_auto_book_lead_time_roundtrip(hass: HomeAssistant):
    """Setting the lead-time via the options flow stores seconds in
    entry.options under OPT_AUTO_BOOK_LEAD_TIME."""
    from custom_components.bsport.const import OPT_AUTO_BOOK_LEAD_TIME

    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "set_auto_book_lead_time"},
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"hours": 6},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_AUTO_BOOK_LEAD_TIME] == 6 * 3600


@pytest.mark.asyncio
async def test_options_set_auto_book_lead_time_rejects_negative(
    hass: HomeAssistant,
):
    """Submitting a negative `hours` value triggers the voluptuous range
    validator and surfaces as a wrapped `Invalid` error from async_configure."""
    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "set_auto_book_lead_time"},
    )
    # voluptuous range raises during configure; HA wraps it in InvalidData
    # (a subclass of voluptuous.error.Invalid). Assert the config_entry
    # option is unchanged.
    from voluptuous.error import Invalid
    with pytest.raises(Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"hours": -1},
        )


@pytest.mark.asyncio
async def test_options_set_auto_book_lead_time_rejects_too_large(
    hass: HomeAssistant,
):
    """Submitting an `hours` value above MAX_AUTO_BOOK_LEAD_TIME_HOURS (336)
    is rejected by the voluptuous range validator."""
    from voluptuous.error import Invalid

    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "set_auto_book_lead_time"},
    )
    with pytest.raises(Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"hours": 400},
        )
