"""Tests for WatchedClassCoordinator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bsport.api import (
    BsportClient,
    Offer,
    WatchedClass,
)
from custom_components.bsport.const import (
    WATCH_PRE_WINDOW_FAR,
    WATCH_PRE_WINDOW_IMMINENT,
    WATCH_PRE_WINDOW_MID,
    WATCH_PRE_WINDOW_NEAR,
)
from custom_components.bsport.coordinator_watch import (
    WatchedClassCoordinator,
    _select_cadence,
)


def _make_offer(
    *,
    bookable_at_delta: timedelta,
    is_bookable_now: bool = False,
    offer_id: int = 99,
) -> Offer:
    now = datetime.now(timezone.utc)
    start = now + timedelta(days=1)
    bookable_at = now + bookable_at_delta
    return Offer(
        offer_id=offer_id,
        class_name="Yoga",
        category="Yoga",
        coach=None,
        start_at=start,
        end_at=start + timedelta(hours=1),
        bookable_at=bookable_at,
        is_bookable_now=is_bookable_now,
        is_waitlist_only=False,
    )


# ── cadence tests ─────────────────────────────────────────────────────────────


def test_cadence_far():
    offer = _make_offer(bookable_at_delta=timedelta(days=3))
    base = WATCH_PRE_WINDOW_FAR
    result = _select_cadence(offer)
    assert base - timedelta(seconds=0.01) <= result <= base * 1.1 + timedelta(seconds=0.01)


def test_cadence_mid():
    offer = _make_offer(bookable_at_delta=timedelta(hours=5))
    base = WATCH_PRE_WINDOW_MID
    result = _select_cadence(offer)
    assert base - timedelta(seconds=0.01) <= result <= base * 1.1 + timedelta(seconds=0.01)


def test_cadence_near():
    offer = _make_offer(bookable_at_delta=timedelta(minutes=30))
    base = WATCH_PRE_WINDOW_NEAR
    result = _select_cadence(offer)
    assert base - timedelta(seconds=0.01) <= result <= base * 1.1 + timedelta(seconds=0.01)


def test_cadence_imminent():
    offer = _make_offer(bookable_at_delta=timedelta(seconds=30))
    base = WATCH_PRE_WINDOW_IMMINENT
    result = _select_cadence(offer)
    assert base - timedelta(seconds=0.01) <= result <= base * 1.1 + timedelta(seconds=0.01)


# ── bookable transition ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_awaiting_to_bookable_fires_class_bookable(
    hass: HomeAssistant,
):
    client = AsyncMock(spec=BsportClient)

    initial_offer = _make_offer(
        bookable_at_delta=timedelta(hours=2), is_bookable_now=False
    )
    bookable_offer = _make_offer(
        bookable_at_delta=timedelta(hours=2), is_bookable_now=True
    )
    client.list_upcoming_offers = AsyncMock(return_value=(bookable_offer,))

    coord = WatchedClassCoordinator(
        hass, client, "e1", studio_id=7, initial_offer=initial_offer
    )
    # Seed prior state with non-bookable status
    coord.data = WatchedClass(offer=initial_offer, status="awaiting_window")

    events: list = []
    hass.bus.async_listen("bsport_class_bookable", lambda e: events.append(e))

    await coord._async_update_data()
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["offer_id"] == 99
    assert events[0].data["entry_id"] == "e1"
