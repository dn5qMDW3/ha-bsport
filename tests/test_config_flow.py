"""Tests for the two-step bsport config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountProfile, BsportAuthError, BsportTransientError,
)
from custom_components.bsport.const import DOMAIN


@pytest.mark.asyncio
async def test_pick_studio_then_happy_path(hass: HomeAssistant):
    profile = AccountProfile(
        bsport_token="tok_40c" + "0" * 33,
        bsport_user_id=9999999,
        studio_id=538,
        studio_name="Chimosa",
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(return_value=profile),
    ):
        # Step 1: pick studio
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": "538"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"

        # Step 2: creds
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "hunter2"},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Chimosa (user@example.com)"
    assert result["data"]["studio_id"] == 538
    assert result["data"]["bsport_user_id"] == 9999999


@pytest.mark.asyncio
async def test_pick_other_then_enter_custom_studio_id(hass: HomeAssistant):
    """Picking 'Other' routes to the custom_studio step which accepts a numeric id."""
    profile = AccountProfile(
        bsport_token="tok", bsport_user_id=42424242,
        studio_id=1234, studio_name="Some Other Studio",
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(return_value=profile),
    ):
        # Step 1: pick studio → "Other" sentinel
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": "__other__"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "custom_studio"

        # Step 2: enter numeric id
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": 1234}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "credentials"

        # Step 3: credentials
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "pw"},
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Some Other Studio (user@example.com)"
    assert result["data"]["studio_id"] == 1234


@pytest.mark.asyncio
async def test_not_a_member_shows_error_on_credentials_step(hass: HomeAssistant):
    # Picks Chimosa from the dropdown but the account belongs to a different studio,
    # so authenticate_and_fetch_profile raises "not a member".
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(side_effect=BsportAuthError(
            "account not a member of studio 538"
        )),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": "538"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "pw"},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "not_a_member"}


@pytest.mark.asyncio
async def test_invalid_auth_shows_error_on_credentials_step(hass: HomeAssistant):
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(side_effect=BsportAuthError("bsport rejected credentials (HTTP 403)")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": "538"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "wrong"},
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_cannot_connect_aborts(hass: HomeAssistant):
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate_and_fetch_profile",
        new=AsyncMock(side_effect=BsportTransientError("nope")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"studio_id": "538"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "x"},
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.asyncio
async def test_duplicate_studio_user_combo_rejected(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="538:9999999",  # new composite format
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
            result["flow_id"], {"studio_id": "538"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "user@example.com", "password": "pw"},
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
