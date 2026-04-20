"""Tests for AccountOverviewCoordinator."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.bsport.api import (
    AccountOverview,
    BsportAuthError,
    BsportClient,
    BsportTransientError,
)
from custom_components.bsport.coordinator_overview import (
    AccountOverviewCoordinator,
)


@pytest.mark.asyncio
async def test_overview_success(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    client.get_account_overview.return_value = AccountOverview(
        waitlists=(), bookings=(), active_pass=None, membership=None,
    )
    coord = AccountOverviewCoordinator(hass, client, entry_id="e1")
    result = await coord._async_update_data()
    assert result.waitlists == ()


@pytest.mark.asyncio
async def test_overview_transient_error_maps_to_update_failed(
    hass: HomeAssistant,
):
    client = AsyncMock(spec=BsportClient)
    client.get_account_overview.side_effect = BsportTransientError("boom")
    coord = AccountOverviewCoordinator(hass, client, entry_id="e1")
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_overview_auth_error_fires_event_and_raises_auth_failed(
    hass: HomeAssistant,
):
    client = AsyncMock(spec=BsportClient)
    client._email = "user@example.com"  # noqa: SLF001
    client.get_account_overview.side_effect = BsportAuthError("bad creds")
    coord = AccountOverviewCoordinator(hass, client, entry_id="e1")

    events: list = []
    hass.bus.async_listen("bsport_auth_failed", lambda e: events.append(e))

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data == {"entry_id": "e1", "email": "user@example.com"}
