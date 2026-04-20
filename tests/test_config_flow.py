"""Tests for the bsport config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountProfile,
    BsportAuthError,
    BsportTransientError,
)
from custom_components.bsport.const import DOMAIN


@pytest.mark.asyncio
async def test_user_flow_happy_path(hass: HomeAssistant):
    profile = AccountProfile(
        bsport_token="tok_40_chars" + "0" * 28,
        bsport_user_id=9999999,
        studio_id=538,
        studio_name="Chimosa",
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(return_value=profile),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "hunter2"},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Chimosa (user@example.com)"
    assert result["data"]["bsport_user_id"] == 9999999
    assert result["options"] == {"watched_offer_ids": []}


@pytest.mark.asyncio
async def test_user_flow_invalid_auth_shows_error(hass: HomeAssistant):
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(side_effect=BsportAuthError("403")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "wrong"},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_flow_cannot_connect_aborts(hass: HomeAssistant):
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(side_effect=BsportTransientError("nope")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "x"},
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_flow_rejects_duplicate_unique_id(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="9999999",
        data={"email": "existing@example.com"},
    ).add_to_hass(hass)
    profile = AccountProfile(
        bsport_token="tok", bsport_user_id=9999999,
        studio_id=538, studio_name="Chimosa",
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(return_value=profile),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "pw"},
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
