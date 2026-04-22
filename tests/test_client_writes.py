"""Tests for BsportClient write methods: register_waitlist, book_offer, cancel_booking."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.bsport.api.client import BsportClient
from custom_components.bsport.api.errors import (
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

_TOKEN = "test_token_fakeauthtoken0000000000000000"

# Frequently used URLs
_USER_REG_URL = f"{BSPORT_API_BASE}/book/v1/offer/user_registration/"
_COMPAT_PACKS_URL = (
    f"{BSPORT_API_BASE}/buyable/v1/payment-pack/consumer-payment-pack/"
    f"compatible_with_offer_unfiltered/?mine=true"
)
_BOOKINGS_FUTURE_URL = f"{BSPORT_API_BASE}/api-v0/booking/future/"
_CANCEL_URL = f"{BSPORT_API_BASE}/book/v1/booking/{BOOKING_RAW['id']}/cancel/"

_BOOKING_FUTURE_PAYLOAD = {
    "count": 1,
    "next": None,
    "previous": None,
    "results": [BOOKING_RAW],
}

_BOOK_SUCCESS_BODY = {
    "offers_booked": [OFFER_RAW["id"]],
    "offer_on_waiting_list": [],
    "error_codes": [],
    "buyable_item_error_code": None,
    "extra_data": [],
}


def _make_client(session: aiohttp.ClientSession) -> BsportClient:
    """Return a pre-authenticated client (skips actual authenticate() call)."""
    client = BsportClient(session, "user@example.com", "pw")
    client._token = _TOKEN
    return client


# ---------------------------------------------------------------------------
# register_waitlist  (→ POST /book/v1/offer/user_registration/)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_waitlist_success_puts_offer_on_waitlist():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                _USER_REG_URL,
                status=200,
                payload={
                    "offers_booked": [],
                    "offer_on_waiting_list": [30362966],
                    "error_codes": [],
                    "buyable_item_error_code": None,
                    "extra_data": [],
                },
            )
            client = _make_client(session)
            result = await client.register_waitlist(offer_id=30362966)

    assert result is None


@pytest.mark.asyncio
async def test_register_waitlist_offer_was_booked_also_success():
    """Server may place the offer in offers_booked if a spot opened in the
    gap between schedule poll and call; still a valid outcome."""
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                _USER_REG_URL,
                status=200,
                payload={
                    "offers_booked": [30362966],
                    "offer_on_waiting_list": [],
                    "error_codes": [],
                    "buyable_item_error_code": None,
                    "extra_data": [],
                },
            )
            client = _make_client(session)
            result = await client.register_waitlist(offer_id=30362966)

    assert result is None


@pytest.mark.asyncio
async def test_register_waitlist_error_code_raises():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                _USER_REG_URL,
                status=200,
                payload={
                    "offers_booked": [],
                    "offer_on_waiting_list": [],
                    "error_codes": ["CANNOT_BOOK"],
                    "buyable_item_error_code": None,
                    "extra_data": [],
                },
            )
            client = _make_client(session)
            with pytest.raises(BsportBookError):
                await client.register_waitlist(offer_id=30362966)


# ---------------------------------------------------------------------------
# book_offer (→ compatible_with_offer_unfiltered + user_registration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_book_offer_no_compatible_packs_raises_no_payment_pack():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_COMPAT_PACKS_URL, status=200, payload=[])
            client = _make_client(session)
            with pytest.raises(BsportBookError) as exc_info:
                await client.book_offer(offer_id=OFFER_RAW["id"])

    assert exc_info.value.reason == "no_payment_pack"


@pytest.mark.asyncio
async def test_book_offer_success_returns_booking():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_COMPAT_PACKS_URL, status=200, payload=[PACK_ACTIVE])
            m.post(_USER_REG_URL, status=200, payload=_BOOK_SUCCESS_BODY)
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            booking = await client.book_offer(offer_id=OFFER_RAW["id"])

    assert isinstance(booking, Booking)
    assert booking.booking_id == BOOKING_RAW["id"]


@pytest.mark.asyncio
async def test_book_offer_class_full_got_waitlisted_raises_cannot_book():
    """When user_registration routes the offer to offer_on_waiting_list
    instead of offers_booked, the class was full. We explicitly called book,
    not register_waitlist — surface that as cannot_book."""
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_COMPAT_PACKS_URL, status=200, payload=[PACK_ACTIVE])
            m.post(
                _USER_REG_URL,
                status=200,
                payload={
                    "offers_booked": [],
                    "offer_on_waiting_list": [OFFER_RAW["id"]],
                    "error_codes": [],
                    "buyable_item_error_code": None,
                    "extra_data": [],
                },
            )
            client = _make_client(session)
            with pytest.raises(BsportBookError) as exc_info:
                await client.book_offer(offer_id=OFFER_RAW["id"])

    assert exc_info.value.reason == "cannot_book"


@pytest.mark.asyncio
async def test_book_offer_error_code_raises():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_COMPAT_PACKS_URL, status=200, payload=[PACK_ACTIVE])
            m.post(
                _USER_REG_URL,
                status=200,
                payload={
                    "offers_booked": [],
                    "offer_on_waiting_list": [],
                    "error_codes": ["OFFER_USER_ALREADY_BOOKED"],
                    "buyable_item_error_code": None,
                    "extra_data": [],
                },
            )
            client = _make_client(session)
            with pytest.raises(BsportBookError):
                await client.book_offer(offer_id=OFFER_RAW["id"])


@pytest.mark.asyncio
async def test_book_offer_transient_5xx_retries_once():
    """A 5xx on the user_registration call triggers a single retry."""
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_COMPAT_PACKS_URL, status=200, payload=[PACK_ACTIVE])
            # First attempt → 500
            m.post(_USER_REG_URL, status=500, body="server error")
            # Retry → 200 with success body
            m.post(_USER_REG_URL, status=200, payload=_BOOK_SUCCESS_BODY)
            m.get(_BOOKINGS_FUTURE_URL, status=200, payload=_BOOKING_FUTURE_PAYLOAD)
            client = _make_client(session)
            booking = await client.book_offer(offer_id=OFFER_RAW["id"])

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
                _USER_REG_URL,
                exception=aiohttp.ClientConnectionError("connection refused"),
            )
            client = _make_client(session)
            with pytest.raises(BsportTransientError):
                await client.register_waitlist(offer_id=30362966)


# ---------------------------------------------------------------------------
# discard_waitlist
# ---------------------------------------------------------------------------


_WAITLIST_DISCARD_URL = (
    f"{BSPORT_API_BASE}/api-v0/waiting-list/booking-option/6549244/discard/"
)


@pytest.mark.asyncio
async def test_discard_waitlist_200_returns_none():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_WAITLIST_DISCARD_URL, status=200, payload={"id": 6549244})
            client = _make_client(session)
            result = await client.discard_waitlist(waitlist_entry_id=6549244)

    assert result is None


@pytest.mark.asyncio
async def test_discard_waitlist_404_is_idempotent_success():
    """If the entry is already gone, treat it as success."""
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(_WAITLIST_DISCARD_URL, status=404, body="")
            client = _make_client(session)
            result = await client.discard_waitlist(waitlist_entry_id=6549244)

    assert result is None


@pytest.mark.asyncio
async def test_discard_waitlist_4xx_raises():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as m:
            m.post(
                _WAITLIST_DISCARD_URL,
                status=400,
                body='{"code": "SOME_ERROR"}',
            )
            client = _make_client(session)
            with pytest.raises(BsportBookError):
                await client.discard_waitlist(waitlist_entry_id=6549244)
