"""Switch entity tests for bsport auto-book toggle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview, Booking, BsportBookError, Offer, WaitlistEntry,
)
from custom_components.bsport.const import (
    CONF_EMAIL, CONF_PASSWORD, CONF_STUDIO_ID, CONF_STUDIO_NAME,
    DOMAIN, OPT_WATCHED_OFFER_IDS,
)


def _offer(*, hours_to_start: float = 48, offer_id: int = 1) -> Offer:
    start = datetime.now(timezone.utc) + timedelta(hours=hours_to_start)
    return Offer(
        offer_id=offer_id, class_name="Pilates", category="Pilates",
        coach="Léa", start_at=start, end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False, is_waitlist_only=True,
    )


def _waitlist(offer: Offer, *, status: str = "convertible") -> WaitlistEntry:
    return WaitlistEntry(
        entry_id=6521868, offer=offer, status=status, position=None,
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


async def _setup_with_waitlist(
    hass: HomeAssistant,
    waitlist: WaitlistEntry,
    *,
    book_mock: AsyncMock | None = None,
) -> MockConfigEntry:
    overview = AccountOverview(
        waitlists=(waitlist,), bookings=(), active_pass=None, membership=None,
    )
    entry = _entry(hass)
    book = book_mock or AsyncMock(
        return_value=Booking(
            booking_id=1, offer=waitlist.offer, status="confirmed",
        )
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_account_overview",
        new=AsyncMock(return_value=overview),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(waitlist,)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    # Bind the book mock directly on the client instance so it stays active
    # for the lifetime of the test (not just during the setup patch block).
    entry.runtime_data.client.book_offer = book  # type: ignore[method-assign]
    entry.runtime_data._test_book = book  # type: ignore[attr-defined]
    return entry


def _switch_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, offer_id: int,
) -> str:
    ent_reg = er.async_get(hass)
    expected_uid = (
        f"{DOMAIN}_{entry.entry_id}_waitlist_autobook_{offer_id}"
    )
    matches = [
        e for e in ent_reg.entities.values() if e.unique_id == expected_uid
    ]
    assert matches, (
        f"switch unique_id {expected_uid!r} not registered. "
        f"Registered: {[e.unique_id for e in ent_reg.entities.values()]}"
    )
    return matches[0].entity_id


# ── 1. Entity registration ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_entity_registered_per_waitlist(hass: HomeAssistant):
    offer = _offer(offer_id=42)
    waitlist = _waitlist(offer, status="waiting")
    entry = await _setup_with_waitlist(hass, waitlist)
    entity_id = _switch_entity_id(hass, entry, 42)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF


# ── 2. Default off ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_defaults_off(hass: HomeAssistant):
    offer = _offer(hours_to_start=48)
    waitlist = _waitlist(offer, status="convertible")
    entry = await _setup_with_waitlist(hass, waitlist)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    assert coord._auto_book_enabled is False
    # And no auto-book was triggered during setup
    assert entry.runtime_data._test_book.await_count == 0


# ── 3. Restore on restart ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_restores_on_state(hass: HomeAssistant):
    """When async_get_last_state returns STATE_ON, async_added_to_hass
    flips the coordinator flag back on."""
    offer = _offer(hours_to_start=48, offer_id=7)
    waitlist = _waitlist(offer, status="waiting")  # not convertible — no auto-book on restore
    fake_state = State("switch.fake_autobook", STATE_ON)
    with patch(
        "custom_components.bsport.switch.WaitlistAutoBookSwitch.async_get_last_state",
        new=AsyncMock(return_value=fake_state),
    ):
        entry = await _setup_with_waitlist(hass, waitlist)
        await hass.async_block_till_done()

    coord = entry.runtime_data.waitlists[offer.offer_id]
    assert coord._auto_book_enabled is True
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 4. Turn ON triggers immediate book when convertible + lead-time OK ──────


@pytest.mark.asyncio
async def test_turn_on_triggers_book_when_conditions_met(
    hass: HomeAssistant,
):
    offer = _offer(hours_to_start=48)  # well outside default 24h lead time
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock(
        return_value=Booking(booking_id=1, offer=offer, status="confirmed")
    )
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    succeeded: list = []
    hass.bus.async_listen(
        "bsport_book_succeeded", lambda e: succeeded.append(e),
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 1
    assert len(succeeded) == 1
    assert succeeded[0].data["source"] == "autobook"


# ── 5. Turn ON, lead-time NOT satisfied → no book ────────────────────────────


@pytest.mark.asyncio
async def test_turn_on_skips_when_inside_lead_time(hass: HomeAssistant):
    offer = _offer(hours_to_start=2)  # inside default 24h lead time
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock()
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 0
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 6. Status-flip on poll triggers book when switch is ON ───────────────────


@pytest.mark.asyncio
async def test_status_transition_triggers_auto_book_when_on(
    hass: HomeAssistant,
):
    offer = _offer(hours_to_start=48, offer_id=11)
    waiting = _waitlist(offer, status="waiting")
    convertible = _waitlist(offer, status="convertible")
    book = AsyncMock(
        return_value=Booking(booking_id=1, offer=offer, status="confirmed")
    )
    entry = await _setup_with_waitlist(hass, waiting, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    # Turn on while still waiting — no book yet.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()
    assert book.await_count == 0

    coord = entry.runtime_data.waitlists[offer.offer_id]
    # Force the next batch fetch to return the convertible state.
    with patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(convertible,)),
    ):
        coord._batch.invalidate()
        await coord.async_refresh()
        await hass.async_block_till_done()

    assert book.await_count == 1


# ── 7. Status-flip with switch OFF → no book ─────────────────────────────────


@pytest.mark.asyncio
async def test_status_transition_does_not_book_when_off(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=12)
    waiting = _waitlist(offer, status="waiting")
    convertible = _waitlist(offer, status="convertible")
    book = AsyncMock()
    entry = await _setup_with_waitlist(hass, waiting, book_mock=book)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    with patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(convertible,)),
    ):
        coord._batch.invalidate()
        await coord.async_refresh()
        await hass.async_block_till_done()

    assert book.await_count == 0


# ── 8. Failure leaves switch on, fires failed event ──────────────────────────


@pytest.mark.asyncio
async def test_failure_leaves_switch_on(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=13)
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock(
        side_effect=BsportBookError(
            reason="payment_required", status=402, raw_body="",
        )
    )
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    failed: list = []
    hass.bus.async_listen(
        "bsport_book_failed", lambda e: failed.append(e),
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 1
    assert len(failed) == 1
    assert failed[0].data["source"] == "autobook"
    assert failed[0].data["reason"] == "payment_required"
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 9. Retry on next poll while still convertible ───────────────────────────


@pytest.mark.asyncio
async def test_retry_on_next_poll_after_failure(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=14)
    waitlist = _waitlist(offer, status="convertible")
    booking = Booking(booking_id=1, offer=offer, status="confirmed")
    book = AsyncMock(
        side_effect=[
            BsportBookError(reason="cannot_book", status=423, raw_body=""),
            BsportBookError(reason="cannot_book", status=423, raw_body=""),
            booking,
        ]
    )
    discard = AsyncMock(return_value=None)
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    with patch(
        "custom_components.bsport.api.client.BsportClient.discard_waitlist",
        new=discard,
    ):
        # First turn on triggers an attempt that fails (cannot_book +
        # convertible → discard → retry → cannot_book again → raise).
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
        )
        await hass.async_block_till_done()
        # Second poll attempt — third book_offer call succeeds.
        coord = entry.runtime_data.waitlists[offer.offer_id]
        with patch(
            "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
            new=AsyncMock(return_value=(waitlist,)),
        ):
            coord._batch.invalidate()
            await coord.async_refresh()
            await hass.async_block_till_done()

    # 1: initial book → cannot_book; 2: discard+retry inside async_book →
    # cannot_book (raises); 3: next poll re-evaluates async_maybe_auto_book
    # → succeeds. The 3 pins the poll-driven retry path specifically.
    assert book.await_count == 3


# ── 10. Lock prevents concurrent auto+manual book ────────────────────────────


@pytest.mark.asyncio
async def test_lock_serialises_concurrent_books(hass: HomeAssistant):
    """When the lock is held, async_maybe_auto_book no-ops; only one book
    proceeds even under contention."""
    offer = _offer(hours_to_start=48, offer_id=15)
    waitlist = _waitlist(offer, status="convertible")
    booking = Booking(booking_id=1, offer=offer, status="confirmed")
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_book(_offer_id):
        started.set()
        await proceed.wait()
        return booking

    book = AsyncMock(side_effect=slow_book)
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    coord._auto_book_enabled = True

    # Kick off a manual book; it'll grab the lock and stall.
    manual = asyncio.create_task(coord.async_book(source="service"))
    await started.wait()
    # Try auto-book while the lock is held — it should no-op.
    await coord.async_maybe_auto_book()
    proceed.set()
    await manual
    await hass.async_block_till_done()

    assert book.await_count == 1  # only the manual call ran
