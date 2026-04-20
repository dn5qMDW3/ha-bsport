"""Tests for BsportClient write methods: register_waitlist, book_offer, cancel_booking."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.bsport.api.client import BsportClient
from custom_components.bsport.api.errors import (
    BsportAuthError,
    BsportBookError,
    BsportTransientError,
)
from custom_components.bsport.api.models import Booking
from custom_components.bsport.const import BSPORT_API_BASE

# ---------------------------------------------------------------------------
# Inline fixtures — self-contained, no imports from other test files
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

BOOKING_RAW = {
    "id": 150281127,
    "pk": 150281127,
    "offer": OFFER_RAW,
    "booking_status_code": 0,
    "status": True,
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

PACK_ACTIVE_2 = {
    "id": 109118928,
    "disabled": False,
    "reverted": False,
    "starting_date": "2026-03-01",
    "ending_date": "2026-04-30",
    "available_credits": 0,
    "used_credits": 0,
}

_TOKEN = "test_token_fakeauthtoken0000000000000000"

# Frequently used URLs
_WAITLIST_REGISTER_URL = f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/register/"
_PACKS_URL = f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/"
_BOOKINGS_FUTURE_URL = f"{BSPORT_API_BASE}/api-v0/booking/future/"
_CANCEL_URL = f"{BSPORT_API_BASE}/book/v1/booking/{BOOKING_RAW['id']}/cancel/"
_BOOK_PACK_URL = f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/{PACK_ACTIVE['id']}/register_booking/"
_BOOK_PACK_URL_2 = f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/{PACK_ACTIVE_2['id']}/register_booking/"

# register_booking response: new booking id in the bookings array
_BOOK_201_PAYLOAD = {
    "id": 999,
    "bookings": [{"id": BOOKING_RAW["id"]}],
}

_BOOKING_FUTURE_PAYLOAD = {
    "count": 1,
    "next": None,
    "previous": None,
    "results": [BOOKING_RAW],
}

_PACKS_PAYLOAD = {
    "count": 1,
    "next": None,
    "previous": None,
    "results": [PACK_ACTIVE],
}


def _make_client(session: aiohttp.ClientSession) -> BsportClient:
    """Return a pre-authenticated client (skips actual authenticate() call)."""
    client = BsportClient(session, "user@example.com", "pw")
    client._token = _TOKEN
    return client


# ---------------------------------------------------------------------------
# register_waitlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_waitlist_success_201():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_WAITLIST_REGISTER_URL, status=201, payload={})
            client = _make_client(session)
            result = await client.register_waitlist(offer_id=30362966)

    assert result is None


@pytest.mark.asyncio
async def test_register_waitlist_duplicate_423_is_idempotent_success():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_WAITLIST_REGISTER_URL, status=423, body="")
            client = _make_client(session)
            result = await client.register_waitlist(offer_id=30362966)

    assert result is None


@pytest.mark.asyncio
async def test_register_waitlist_other_4xx_raises_book_error():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_WAITLIST_REGISTER_URL, status=400, body='{"code": "SOME_ERROR"}')
            client = _make_client(session)
            with pytest.raises(BsportBookError):
                await client.register_waitlist(offer_id=30362966)


# ---------------------------------------------------------------------------
# book_offer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_book_offer_no_packs_raises_no_payment_pack():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(
                _PACKS_URL,
                status=200,
                payload={"count": 0, "next": None, "previous": None, "results": []},
            )
            client = _make_client(session)
            with pytest.raises(BsportBookError) as exc_info:
                await client.book_offer(offer_id=30362966)

    assert exc_info.value.reason == "no_payment_pack"


@pytest.mark.asyncio
async def test_book_offer_all_packs_423_raises_cannot_book():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_PACKS_URL, status=200, payload=_PACKS_PAYLOAD)
            m.post(_BOOK_PACK_URL, status=423, body="")
            client = _make_client(session)
            with pytest.raises(BsportBookError) as exc_info:
                await client.book_offer(offer_id=30362966)

    assert exc_info.value.reason == "cannot_book"
    assert exc_info.value.status == 423


@pytest.mark.asyncio
async def test_book_offer_first_pack_201_returns_booking():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_PACKS_URL, status=200, payload=_PACKS_PAYLOAD)
            m.post(_BOOK_PACK_URL, status=201, payload=_BOOK_201_PAYLOAD)
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            booking = await client.book_offer(offer_id=30362966)

    assert isinstance(booking, Booking)
    assert booking.booking_id == BOOKING_RAW["id"]


@pytest.mark.asyncio
async def test_book_offer_first_pack_423_second_pack_201():
    two_packs_payload = {
        "count": 2,
        "next": None,
        "previous": None,
        "results": [PACK_ACTIVE, PACK_ACTIVE_2],
    }
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_PACKS_URL, status=200, payload=two_packs_payload)
            m.post(_BOOK_PACK_URL, status=423, body="")
            m.post(_BOOK_PACK_URL_2, status=201, payload=_BOOK_201_PAYLOAD)
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            booking = await client.book_offer(offer_id=30362966)

    assert isinstance(booking, Booking)
    assert booking.booking_id == BOOKING_RAW["id"]


@pytest.mark.asyncio
async def test_book_offer_transient_5xx_retries_once():
    """A 5xx on first attempt triggers a single retry that succeeds."""
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_PACKS_URL, status=200, payload=_PACKS_PAYLOAD)
            # First attempt → 500
            m.post(_BOOK_PACK_URL, status=500, body="server error")
            # Retry → 201
            m.post(_BOOK_PACK_URL, status=201, payload=_BOOK_201_PAYLOAD)
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            booking = await client.book_offer(offer_id=30362966)

    assert isinstance(booking, Booking)
    assert booking.booking_id == BOOKING_RAW["id"]


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_booking_resolves_offer_id_then_posts_cancel():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            m.post(_CANCEL_URL, status=200, payload={})
            client = _make_client(session)
            result = await client.cancel_booking(offer_id=OFFER_RAW["id"])

    assert result is None


@pytest.mark.asyncio
async def test_cancel_booking_offer_not_found_raises_book_error():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            with pytest.raises(BsportBookError) as exc_info:
                await client.cancel_booking(offer_id=99999999)

    assert exc_info.value.reason == "unknown_client_error"


@pytest.mark.asyncio
async def test_cancel_booking_cancel_endpoint_4xx_raises():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            m.post(_CANCEL_URL, status=400, body='{"code": "SOME_ERROR"}')
            client = _make_client(session)
            with pytest.raises(BsportBookError):
                await client.cancel_booking(offer_id=OFFER_RAW["id"])


# ---------------------------------------------------------------------------
# cross-cutting: network errors raise BsportTransientError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_methods_raise_transient_on_network_error():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                _WAITLIST_REGISTER_URL,
                exception=aiohttp.ClientConnectionError("connection refused"),
            )
            client = _make_client(session)
            with pytest.raises(BsportTransientError):
                await client.register_waitlist(offer_id=30362966)
