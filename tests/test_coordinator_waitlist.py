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
    WaitlistBatchCache,
    WaitlistEntryCoordinator,
    _select_cadence,
)


def _fake_batch(entry: WaitlistEntry | None) -> WaitlistBatchCache:
    """Return a WaitlistBatchCache-shaped mock that serves `entry` by offer_id.

    The real cache does the HTTP fan-in; tests don't exercise that path —
    they just need `get_entry(offer_id)` to return a canned response.
    """
    cache = AsyncMock(spec=WaitlistBatchCache)
    cache.get_entry = AsyncMock(return_value=entry)
    cache.invalidate = lambda: None
    return cache


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


# ── batch cache coalescing ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_cache_coalesces_concurrent_lookups():
    """Multiple concurrent get_entry() calls share a single client fetch.

    This is the whole point of the cache: when N per-offer coordinators tick
    in sync, we make 1 pair of HTTP requests, not N.
    """
    import asyncio as _aio
    client = AsyncMock(spec=BsportClient)
    e1 = _entry(timedelta(hours=3), offer_id=1)
    e2 = _entry(timedelta(hours=3), offer_id=2)
    e3 = _entry(timedelta(hours=3), offer_id=3)
    client.list_waitlists_with_positions = AsyncMock(return_value=(e1, e2, e3))

    cache = WaitlistBatchCache(client)
    # Three concurrent lookups — should fold into one underlying fetch.
    r1, r2, r3 = await _aio.gather(
        cache.get_entry(1), cache.get_entry(2), cache.get_entry(3),
    )

    assert r1.offer.offer_id == 1
    assert r2.offer.offer_id == 2
    assert r3.offer.offer_id == 3
    assert client.list_waitlists_with_positions.await_count == 1


@pytest.mark.asyncio
async def test_batch_cache_invalidate_forces_refetch():
    client = AsyncMock(spec=BsportClient)
    e1 = _entry(timedelta(hours=3), offer_id=1)
    client.list_waitlists_with_positions = AsyncMock(return_value=(e1,))

    cache = WaitlistBatchCache(client)
    await cache.get_entry(1)
    cache.invalidate()
    await cache.get_entry(1)

    assert client.list_waitlists_with_positions.await_count == 2


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

    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=waiting,
        batch_cache=_fake_batch(convertible),
    )
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
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
    )
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

    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
    )

    events: list = []
    hass.bus.async_listen("bsport_book_failed", lambda e: events.append(e))

    with pytest.raises(BsportBookError):
        await coord.async_book(source="service")
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["reason"] == "cannot_book"
    assert events[0].data["source"] == "service"


# ── convertible fallback: discard then re-book ───────────────────────────────


@pytest.mark.asyncio
async def test_convertible_cannot_book_triggers_discard_and_retry(
    hass: HomeAssistant,
):
    """When the server still marks the class full while our waitlist is
    convertible, the coordinator discards the waitlist reservation and
    retries the normal booking."""
    client = AsyncMock(spec=BsportClient)
    convertible = _entry(timedelta(hours=3), status="convertible", position=1)
    # First book_offer call fails with cannot_book, second succeeds.
    booking = Booking(booking_id=77, offer=convertible.offer, status="confirmed")
    client.book_offer = AsyncMock(
        side_effect=[
            BsportBookError(reason="cannot_book", status=423, raw_body=""),
            booking,
        ]
    )
    client.discard_waitlist = AsyncMock(return_value=None)

    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
    )
    coord.data = convertible  # enables the convertible-state guard

    succeeded: list = []
    failed: list = []
    hass.bus.async_listen("bsport_book_succeeded", lambda e: succeeded.append(e))
    hass.bus.async_listen("bsport_book_failed", lambda e: failed.append(e))

    await coord.async_book(source="waitlist")
    await hass.async_block_till_done()

    client.discard_waitlist.assert_awaited_once_with(convertible.entry_id)
    assert client.book_offer.await_count == 2
    assert len(succeeded) == 1
    assert not failed


@pytest.mark.asyncio
async def test_convertible_cannot_book_discard_also_fails_raises_original(
    hass: HomeAssistant,
):
    """If the discard step fails, don't retry — surface the original book
    error so the user still sees something actionable and no waitlist spot
    is lost."""
    client = AsyncMock(spec=BsportClient)
    convertible = _entry(timedelta(hours=3), status="convertible", position=1)
    client.book_offer = AsyncMock(
        side_effect=BsportBookError(reason="cannot_book", status=423, raw_body="")
    )
    client.discard_waitlist = AsyncMock(
        side_effect=BsportBookError(reason="unknown_client_error", status=500, raw_body="")
    )

    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
    )
    coord.data = convertible

    events: list = []
    hass.bus.async_listen("bsport_book_failed", lambda e: events.append(e))

    with pytest.raises(BsportBookError) as exc_info:
        await coord.async_book(source="waitlist")
    await hass.async_block_till_done()

    # Only the original cannot_book error surfaces — not the discard failure.
    assert exc_info.value.reason == "cannot_book"
    assert client.book_offer.await_count == 1  # no retry
    assert len(events) == 1
    assert events[0].data["reason"] == "cannot_book"


@pytest.mark.asyncio
async def test_waiting_state_cannot_book_does_not_discard(hass: HomeAssistant):
    """The discard+book fallback must NOT fire while still in `waiting` —
    that would throw away a queue position for no reason."""
    client = AsyncMock(spec=BsportClient)
    waiting = _entry(timedelta(hours=3), status="waiting", position=3)
    client.book_offer = AsyncMock(
        side_effect=BsportBookError(reason="cannot_book", status=423, raw_body="")
    )
    client.discard_waitlist = AsyncMock(return_value=None)

    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=waiting,
        batch_cache=_fake_batch(waiting),
    )
    coord.data = waiting

    with pytest.raises(BsportBookError):
        await coord.async_book(source="service")

    client.discard_waitlist.assert_not_awaited()
    assert client.book_offer.await_count == 1


# ── auto-book wiring ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_coordinator_init_accepts_auto_book_lead_time(
    hass: HomeAssistant,
):
    """Constructor takes a lead-time timedelta and stores it; default is 24h."""
    client = AsyncMock(spec=BsportClient)
    initial = _entry(timedelta(hours=3))

    coord_default = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
    )
    assert coord_default._auto_book_lead_time == timedelta(hours=24)
    assert coord_default._auto_book_enabled is False

    coord_custom = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
        auto_book_lead_time=timedelta(hours=2),
    )
    assert coord_custom._auto_book_lead_time == timedelta(hours=2)
