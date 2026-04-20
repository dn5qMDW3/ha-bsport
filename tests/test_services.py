"""Tests for bsport services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview,
    Booking,
    BsportBookError,
    BsportClient,
    Offer,
)
from custom_components.bsport.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    CONF_STUDIO_NAME,
    DOMAIN,
    OPT_WATCHED_OFFER_IDS,
)
from custom_components.bsport.coordinator_overview import AccountOverviewCoordinator
from custom_components.bsport.services import async_register_services


def _make_offer() -> Offer:
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    return Offer(
        offer_id=1,
        class_name="X",
        category="X",
        coach=None,
        start_at=start,
        end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=True,
        is_waitlist_only=False,
    )


def _build_entry_with_runtime(hass: HomeAssistant, *, book_mock=None, cancel_mock=None):
    """Build a MockConfigEntry and attach fake runtime_data to it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "pw",
            "bsport_token": "tok",
            "bsport_user_id": 9999999,
            CONF_STUDIO_ID: 538,
            CONF_STUDIO_NAME: "Chimosa",
        },
        options={OPT_WATCHED_OFFER_IDS: []},
        unique_id="9999999",
    )
    entry.add_to_hass(hass)

    offer = _make_offer()
    client = AsyncMock(spec=BsportClient)
    client.book_offer = book_mock or AsyncMock(
        return_value=Booking(booking_id=1, offer=offer, status="confirmed")
    )
    client.cancel_booking = cancel_mock or AsyncMock(return_value=None)

    empty_overview = AccountOverview(
        waitlists=(), bookings=(), active_pass=None, membership=None
    )
    overview_coord = MagicMock(spec=AccountOverviewCoordinator)
    overview_coord.data = empty_overview
    overview_coord.async_request_refresh = AsyncMock(return_value=None)

    # Build a minimal runtime_data namespace.
    from custom_components.bsport import BsportRuntimeData
    runtime = BsportRuntimeData(
        client=client,
        overview=overview_coord,
    )
    entry.runtime_data = runtime

    return entry, client


@pytest.mark.asyncio
async def test_book_offer_service_dispatches_to_client(hass: HomeAssistant):
    async_register_services(hass)
    entry, client = _build_entry_with_runtime(hass)

    await hass.services.async_call(
        DOMAIN,
        "book_offer",
        {"entry_id": entry.entry_id, "offer_id": 42},
        blocking=True,
    )

    client.book_offer.assert_awaited_with(42)


@pytest.mark.asyncio
async def test_cancel_booking_service_dispatches_to_client(hass: HomeAssistant):
    async_register_services(hass)
    entry, client = _build_entry_with_runtime(hass)

    await hass.services.async_call(
        DOMAIN,
        "cancel_booking",
        {"entry_id": entry.entry_id, "offer_id": 42},
        blocking=True,
    )

    client.cancel_booking.assert_awaited_with(42)


@pytest.mark.asyncio
async def test_book_offer_service_raises_on_book_error(hass: HomeAssistant):
    async_register_services(hass)
    failing = AsyncMock(
        side_effect=BsportBookError(reason="no_payment_pack", status=0, raw_body="")
    )
    entry, _ = _build_entry_with_runtime(hass, book_mock=failing)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "book_offer",
            {"entry_id": entry.entry_id, "offer_id": 42},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_watch_class_service_updates_options(hass: HomeAssistant):
    async_register_services(hass)
    entry, _ = _build_entry_with_runtime(hass)

    await hass.services.async_call(
        DOMAIN,
        "watch_class",
        {"entry_id": entry.entry_id, "offer_id": 99},
        blocking=True,
    )

    assert 99 in entry.options[OPT_WATCHED_OFFER_IDS]
