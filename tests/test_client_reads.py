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

# What /book/v1/offer/?company=X&date=Y actually returns — a flatter shape
# where `activity` is just the integer id, `activity_name` is top-level, and
# the "full" flag is literally named `full` instead of `is_full`. Confirmed
# against the live API.
OFFER_RAW_FLAT = {
    "id": 30671008,
    "date_start": "2026-04-27T08:00:00+02:00",
    "duration_minute": 60,
    "activity": 963054,  # int, not a dict
    "activity_name": "Muay Thai - All levels",
    "coach": 105538,  # int id, not a nested object
    "establishment": 1859,
    "company": 538,
    "available": True,
    "full": False,  # NB: different key name from nested shape
    "is_waiting_list_full": False,
    "timezone_name": "Europe/Berlin",
    "effectif": 20,
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


def test_parse_offer_handles_flat_schedule_shape():
    """The /book/v1/offer/ schedule endpoint returns activity as a bare int
    and uses `full` instead of `is_full`. The parser must not crash on that.

    Regression test for AttributeError: 'int' object has no attribute 'get'
    during options-flow add_watch on a real HA instance.
    """
    offer = parse_offer(OFFER_RAW_FLAT)
    assert offer.offer_id == 30671008
    assert offer.class_name == "Muay Thai - All levels"
    assert offer.category == ""  # not surfaced in the flat shape
    assert offer.coach is None  # coach is an int id, no name to extract
    assert offer.is_bookable_now is True
    assert offer.is_waitlist_only is False

    tz_plus2 = timezone(timedelta(hours=2))
    expected_start = datetime(2026, 4, 27, 8, 0, 0, tzinfo=tz_plus2)
    assert offer.start_at == expected_start
    assert offer.end_at == expected_start + timedelta(minutes=60)


def test_parse_offer_handles_flat_shape_when_class_is_full():
    """Flat shape `full: True` flips is_bookable_now off and marks waitlist-only."""
    raw = {
        **OFFER_RAW_FLAT,
        "id": 99999,
        "available": True,
        "full": True,
        "is_waiting_list_full": False,
    }
    offer = parse_offer(raw)
    assert offer.is_bookable_now is False
    assert offer.is_waitlist_only is True


def test_parse_offer_extracts_cover_url_from_nested_shape():
    """Nested activity with both thumbnail + main — prefer thumbnail."""
    raw = {
        **OFFER_RAW,
        "activity": {
            **OFFER_RAW["activity"],
            "cover_thumbnail": "https://cdn.example/thumb.jpg",
            "cover_main": "https://cdn.example/main.jpg",
        },
    }
    offer = parse_offer(raw)
    assert offer.cover_url == "https://cdn.example/thumb.jpg"


def test_parse_offer_cover_falls_back_to_main_when_no_thumbnail():
    raw = {
        **OFFER_RAW,
        "activity": {
            **OFFER_RAW["activity"],
            "cover_main": "https://cdn.example/main.jpg",
        },
    }
    offer = parse_offer(raw)
    assert offer.cover_url == "https://cdn.example/main.jpg"


def test_parse_offer_cover_is_none_on_flat_schedule_shape():
    """The flat /book/v1/offer/ response does not carry cover URLs."""
    offer = parse_offer(OFFER_RAW_FLAT)
    assert offer.cover_url is None


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
# list_waitlists_with_positions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_waitlists_with_positions_batches_position_lookup():
    """One list call + one batched position call, no per-offer fan-out."""
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"
    pos_url = (
        f"{BSPORT_API_BASE}/book/v1/offer/waiting_list_position_list/"
        f"?id__in=30362966"
    )

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(waitlist_url, status=200, payload=[WAITLIST_RAW])
            m.get(
                pos_url,
                status=200,
                payload={
                    "links": {"next": None, "previous": None},
                    "next_page": None,
                    "page": 1,
                    "count": 1,
                    "results": [
                        {
                            "id": 30362966,
                            "waiting_list_position": {
                                "member_position": 2,
                                "waiting_list_size": 5,
                                "dynamic": 1,
                            },
                        }
                    ],
                },
            )
            result = await client.list_waitlists_with_positions()

    assert len(result) == 1
    entry = result[0]
    assert entry.entry_id == 6521868
    assert entry.offer.offer_id == 30362966
    assert entry.position == 2
    assert entry.waiting_list_size == 5
    assert entry.dynamic == 1


@pytest.mark.asyncio
async def test_list_waitlists_with_positions_empty_list_skips_batch_call():
    """When the user has no waitlist entries, skip the position call entirely."""
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(waitlist_url, status=200, payload=[])
            result = await client.list_waitlists_with_positions()

    assert result == ()


@pytest.mark.asyncio
async def test_list_waitlists_with_positions_tolerates_position_failure():
    """If the batched position lookup errors, entries still come back
    with position fields as None."""
    waitlist_url = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/"
    pos_url = (
        f"{BSPORT_API_BASE}/book/v1/offer/waiting_list_position_list/"
        f"?id__in=30362966"
    )

    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            client = await _authenticated_client(session, m)
            m.get(waitlist_url, status=200, payload=[WAITLIST_RAW])
            m.get(pos_url, status=500, body="")
            result = await client.list_waitlists_with_positions()

    assert len(result) == 1
    entry = result[0]
    assert entry.offer.offer_id == 30362966
    assert entry.position is None
    assert entry.waiting_list_size is None
    assert entry.dynamic is None
