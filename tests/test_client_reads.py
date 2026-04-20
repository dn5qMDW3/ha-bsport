"""Tests for BsportClient read methods and parsers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.bsport.api.client import BsportClient
from custom_components.bsport.api.parsers import (
    parse_booking,
    parse_membership,
    parse_offer,
    parse_waitlist_entry,
)
from custom_components.bsport.const import BSPORT_API_BASE

# ---------------------------------------------------------------------------
# Inline fixtures — no file I/O
# ---------------------------------------------------------------------------

OFFER_RAW = {
    "id": 30362966,
    "date_start": "2026-04-20T18:30:00+02:00",
    "duration_minute": 60,
    "available": True,
    "is_full": False,
    "is_waiting_list_full": False,
    "timezone_name": "Europe/Berlin",
    "activity": {
        "name": "Tai Chi",
        "category": "Tai Chi (de)",
        "coach": {"name": "Jie Rui Zhang"},
        "company": 538,
        "company_name": "Chimosa",
    },
}

WAITLIST_RAW = {
    "id": 6521868,
    "is_convertible": True,
    "cancelled": False,
    "booking": None,
    "offer": OFFER_RAW,
}

BOOKING_RAW = {
    "id": 150281127,
    "pk": 150281127,
    "offer": OFFER_RAW,
    "booking_status_code": 0,
    "status": True,
}

MEMBERSHIP_RAW = {
    "id": 12345,
    "company": 538,
    "company_name": "Chimosa",
    "name": "Chimosa Unlimited",
    "user_id": 9999999,
    "consumer": 9999999,
}

PACK_ACTIVE = {
    "id": 109118927,
    "disabled": False,
    "reverted": False,
    "starting_date": "2026-04-11",
    "ending_date": "2026-05-10",
    "available_credits": 0,
    "used_credits": 0,
}

PACK_EXPIRED = {
    "id": 100000000,
    "disabled": False,
    "reverted": False,
    "starting_date": "2020-01-01",
    "ending_date": "2020-02-01",
}

PACK_DISABLED = {
    "id": 100000001,
    "disabled": True,
    "reverted": False,
    "starting_date": "2026-04-11",
    "ending_date": "2026-05-10",
}

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_offer_extracts_all_fields():
    offer = parse_offer(OFFER_RAW)
    assert offer.offer_id == 30362966
    assert offer.class_name == "Tai Chi"
    assert offer.category == "Tai Chi (de)"
    assert offer.coach == "Jie Rui Zhang"

    tz_plus2 = timezone(timedelta(hours=2))
    expected_start = datetime(2026, 4, 20, 18, 30, 0, tzinfo=tz_plus2)
    assert offer.start_at == expected_start
    assert offer.end_at == expected_start + timedelta(minutes=60)
    assert offer.bookable_at == expected_start - timedelta(days=14)

    assert offer.is_bookable_now is True
    assert offer.is_waitlist_only is False


def test_parse_waitlist_entry_convertible():
    entry = parse_waitlist_entry(WAITLIST_RAW)
    assert entry.entry_id == 6521868
    assert entry.status == "convertible"
    assert entry.position is None


def test_parse_waitlist_entry_already_booked():
    raw = {**WAITLIST_RAW, "booking": 99999, "is_convertible": False}
    entry = parse_waitlist_entry(raw)
    assert entry.status == "already_booked"


def test_parse_waitlist_entry_expired():
    raw = {**WAITLIST_RAW, "cancelled": True}
    entry = parse_waitlist_entry(raw)
    assert entry.status == "expired"


def test_parse_waitlist_entry_waiting():
    raw = {**WAITLIST_RAW, "is_convertible": False, "cancelled": False, "booking": None}
    entry = parse_waitlist_entry(raw)
    assert entry.status == "waiting"


@pytest.mark.parametrize(
    "code, expected_status",
    [
        (0, "confirmed"),
        (1, "attended"),
        (2, "cancelled"),
        (3, "noshow"),
    ],
)
def test_parse_booking_status_codes(code, expected_status):
    raw = {**BOOKING_RAW, "booking_status_code": code}
    booking = parse_booking(raw)
    assert booking.booking_id == 150281127
    assert booking.status == expected_status


def test_parse_membership_always_active():
    membership = parse_membership(MEMBERSHIP_RAW)
    assert membership.status == "active"
    assert membership.product_name == "Chimosa Unlimited"
    assert membership.next_renewal_at is None


# ---------------------------------------------------------------------------
# Client read-method tests — helpers
# ---------------------------------------------------------------------------

SIGNIN_URL = f"{BSPORT_API_BASE}/platform/v1/authentication/signin/with-login/"
SIGNIN_PAYLOAD = {
    "status": "ok",
    "firebaseToken": "fake",
    "token": "tok_40_chars_hex_" + "0" * 23,
    "email_confirmed": True,
    "is_staff": False,
    "is_superuser": False,
}


async def _authenticated_client(session: aiohttp.ClientSession, m: aioresponses) -> BsportClient:
    """Register a signin mock and return an authenticated BsportClient."""
    m.post(SIGNIN_URL, status=200, payload=SIGNIN_PAYLOAD)
    client = BsportClient(session, "user@example.com", "pw")
    await client.authenticate()
    return client


# ---------------------------------------------------------------------------
# get_account_overview
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_account_overview_composes_all_three_fans():
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"
    bookings_url = f"{BSPORT_API_BASE}/api-v0/booking/future/"
    membership_url = f"{BSPORT_API_BASE}/core-data/v1/membership/"

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)

            m.get(
                waitlist_url,
                status=200,
                payload=[WAITLIST_RAW],
            )
            m.get(
                bookings_url,
                status=200,
                payload={"count": 1, "next": None, "previous": None, "results": [BOOKING_RAW]},
            )
            m.get(
                membership_url,
                status=200,
                payload={"count": 1, "next": None, "previous": None, "results": [MEMBERSHIP_RAW]},
            )

            overview = await client.get_account_overview()

    assert len(overview.waitlists) == 1
    assert len(overview.bookings) == 1
    assert overview.membership is not None
    assert overview.membership.status == "active"
    assert overview.active_pass is None


# ---------------------------------------------------------------------------
# list_upcoming_offers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_upcoming_offers_returns_tuple_of_offer():
    offers_url = f"{BSPORT_API_BASE}/book/v1/offer/?company=538&date=2026-04-27"

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)

            m.get(
                offers_url,
                status=200,
                payload={"count": 1, "next": None, "previous": None, "results": [OFFER_RAW]},
            )

            result = await client.list_upcoming_offers(company=538, date="2026-04-27")

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert result[0].offer_id == 30362966


# ---------------------------------------------------------------------------
# get_waitlist_entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_waitlist_entry_matches_offer_id():
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(waitlist_url, status=200, payload=[WAITLIST_RAW])
            entry = await client.get_waitlist_entry(offer_id=30362966)

    assert entry is not None
    assert entry.entry_id == 6521868
    assert entry.offer.offer_id == 30362966


@pytest.mark.asyncio
async def test_get_waitlist_entry_returns_none_when_not_found():
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(waitlist_url, status=200, payload=[WAITLIST_RAW])
            entry = await client.get_waitlist_entry(offer_id=99999999)

    assert entry is None


# ---------------------------------------------------------------------------
# list_active_packs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_active_packs_filters_and_orders():
    packs_url = f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/"

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(
                packs_url,
                status=200,
                payload={
                    "count": 3,
                    "next": None,
                    "previous": None,
                    "results": [PACK_ACTIVE, PACK_EXPIRED, PACK_DISABLED],
                },
            )
            result = await client.list_active_packs()

    assert isinstance(result, tuple)
    assert len(result) == 1
    assert result[0]["id"] == 109118927
