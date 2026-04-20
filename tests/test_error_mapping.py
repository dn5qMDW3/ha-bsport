"""Table-driven tests for bsport error constant → normalized reason mapping."""
import pytest

from custom_components.bsport.api.errors import (
    BsportBookError,
    BsportRateLimited,
    normalize_book_error,
)


@pytest.mark.parametrize(
    "bsport_code, expected_reason",
    [
        ("OFFER_WAITING_LIST_CAN_NOT_BOOK_TOO_MANY_FUTURE", "too_many_future_bookings"),
        ("OFFER_WAITING_LIST_NO_USABLE_CONSUMER_PAYMENT_PACK", "no_payment_pack"),
        ("OFFER_WAITING_LIST_LOCKED_BY_PENDING_BOOKINGS", "locked_pending"),
        ("OFFER_NO_LONGER_CONVERTIBLE", "spot_taken"),
        ("SOMETHING_WE_HAVENT_SEEN_BEFORE", "unknown_client_error"),
    ],
)
def test_normalize_book_error(bsport_code, expected_reason):
    err = normalize_book_error(bsport_code, status=400, raw_body="{}")
    assert isinstance(err, BsportBookError)
    assert err.reason == expected_reason


def test_normalize_423_maps_to_cannot_book():
    # Real-world: bsport returns 423 Locked with empty body when the user
    # cannot book (weekly cap hit, class full, pack exhausted). Map to the
    # dedicated "cannot_book" reason.
    err = normalize_book_error(None, status=423, raw_body="")
    assert isinstance(err, BsportBookError)
    assert err.reason == "cannot_book"


def test_normalize_rate_limited_on_429():
    err = normalize_book_error(None, status=429, raw_body="", retry_after="30")
    assert isinstance(err, BsportRateLimited)
    assert err.retry_after == 30.0


def test_normalize_rate_limited_default_retry_after():
    err = normalize_book_error(None, status=429, raw_body="")
    assert isinstance(err, BsportRateLimited)
    assert err.retry_after == 60.0
