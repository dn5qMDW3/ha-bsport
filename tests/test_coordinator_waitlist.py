"""Tests for WaitlistEntryCoordinator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bsport.api import (
    Booking,
    BsportBookError,
    BsportClient,
    Offer,
    WaitlistEntry,
)
from custom_components.bsport.const import (
    WAITLIST_INTERVAL_BEYOND_24H,
    WAITLIST_INTERVAL_UNDER_24H,
    WAITLIST_INTERVAL_UNDER_2H,
)
from custom_components.bsport.coordinator_waitlist import (
    WaitlistEntryCoordinator,
    _select_cadence,
)


def _make_offer(start_delta: timedelta, *, offer_id: int = 42) -> Offer:
    now = datetime.now(timezone.utc)
    start = now + start_delta
    return Offer(
        offer_id=offer_id,
        class_name="X",
        category="X",
        coach=None,
        start_at=start,
        end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False,
        is_waitlist_only=True,
    )


def _entry(
    start_delta: timedelta,
    *,
    status: str = "waiting",
    position: int = 3,
    offer_id: int = 42,
) -> WaitlistEntry:
    return WaitlistEntry(
        entry_id=1,
        offer=_make_offer(start_delta, offer_id=offer_id),
        status=status,  # type: ignore[arg-type]
        position=position,
    )


# ── cadence tests ────────────────────────────────────────────────────────────


def test_cadence_under_2h():
    base = WAITLIST_INTERVAL_UNDER_2H
    offer = _make_offer(timedelta(minutes=30))
    result = _select_cadence(offer.start_at)
    lower = base * 1.0 - timedelta(seconds=0.01)
    upper = base * 1.1 + timedelta(seconds=0.01)
    assert lower <= result <= upper


def test_cadence_under_24h():
    base = WAITLIST_INTERVAL_UNDER_24H
    offer = _make_offer(timedelta(hours=10))
    result = _select_cadence(offer.start_at)
    lower = base * 1.0 - timedelta(seconds=0.01)
    upper = base * 1.1 + timedelta(seconds=0.01)
    assert lower <= result <= upper


def test_cadence_beyond_24h():
    base = WAITLIST_INTERVAL_BEYOND_24H
    offer = _make_offer(timedelta(days=3))
    result = _select_cadence(offer.start_at)
    lower = base * 1.0 - timedelta(seconds=0.01)
    upper = base * 1.1 + timedelta(seconds=0.01)
    assert lower <= result <= upper


# ── status transition ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_transition_fires_spot_open(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    waiting = _entry(timedelta(hours=3), status="waiting", position=3)
    convertible = _entry(timedelta(hours=3), status="convertible", position=1)
    client.get_waitlist_entry = AsyncMock(return_value=convertible)

    coord = WaitlistEntryCoordinator(hass, client, "e1", initial=waiting)
    coord.data = waiting  # fake prior state

    events: list = []
    hass.bus.async_listen("bsport_spot_open", lambda e: events.append(e))

    await coord._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["entry_id"] == "e1"
    assert data["offer_id"] == 42
    assert data["position_was"] == 3


# ── book success ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_book_success_fires_event(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    initial = _entry(timedelta(hours=5))
    offer = initial.offer
    booking = Booking(booking_id=1, offer=offer, status="confirmed")
    client.book_offer = AsyncMock(return_value=booking)
    # async_request_refresh will call _async_update_data; avoid infinite loop
    client.get_waitlist_entry = AsyncMock(return_value=initial)

    coord = WaitlistEntryCoordinator(hass, client, "e1", initial=initial)
    coord.data = initial

    events: list = []
    hass.bus.async_listen("bsport_book_succeeded", lambda e: events.append(e))

    await coord.async_book(source="waitlist")
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["offer_id"] == 42
    assert events[0].data["source"] == "waitlist"


# ── book failure ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_book_failure_fires_failed_event(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    initial = _entry(timedelta(hours=5))
    client.book_offer = AsyncMock(
        side_effect=BsportBookError(
            reason="cannot_book", status=423, raw_body=""
        )
    )

    coord = WaitlistEntryCoordinator(hass, client, "e1", initial=initial)

    events: list = []
    hass.bus.async_listen("bsport_book_failed", lambda e: events.append(e))

    with pytest.raises(BsportBookError):
        await coord.async_book(source="service")
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["reason"] == "cannot_book"
    assert events[0].data["source"] == "service"
