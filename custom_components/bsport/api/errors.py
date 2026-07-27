"""bsport API exception hierarchy and error normalization.

Design notes from live API recon:
- 423 Locked with empty body means "you cannot book right now": class full,
  weekly cap reached, or pack exhausted. There is no single error code in
  the response body; the status alone carries the signal. We map it to
  BsportBookError(reason="cannot_book") — the user should be directed to
  the waitlist flow.
- 429 with Retry-After header is the rate-limit signal (default 60 s).
- Typed 4xx error codes come in as a "code" field in the JSON body; we
  translate a curated set into normalized reasons for HA event payloads.
- `/book/v1/offer/user_registration/` is different: it answers 200 with an
  `error_codes` list of `[offer_id, numeric_code]` pairs. Those numeric
  codes are a *separate* namespace from the string "code" field above, so
  both maps below are consulted. The numeric values were read out of the
  Chimosa 7.34.1 Android bundle's error-constant module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BookErrorReason = Literal[
    "too_many_future_bookings",
    "no_payment_pack",
    "locked_pending",
    "spot_taken",
    "cannot_book",
    "rate_limited",
    "booking_limit_reached",
    "no_credit",
    "pack_disabled",
    "waitlist_full",
    "already_booked",
    "spot_unavailable",
    "unknown_client_error",
]


class BsportError(Exception):
    """Base for everything the client raises."""


class BsportAuthError(BsportError):
    """Auth failed even after a refresh/re-auth attempt."""


class BsportTransientError(BsportError):
    """Transient network or 5xx failure. Coordinators re-raise as UpdateFailed."""


class BsportRateLimited(BsportTransientError):
    """429 from the server. ``retry_after`` is seconds."""

    def __init__(self, retry_after: float):
        super().__init__(f"Rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass
class BsportBookError(BsportError):
    """Known 4xx while booking. ``reason`` is the normalized string."""

    reason: BookErrorReason
    status: int
    raw_body: str

    def __str__(self) -> str:
        return f"bsport book error ({self.reason}, HTTP {self.status})"


_BSPORT_CODE_TO_REASON: dict[str, BookErrorReason] = {
    "OFFER_WAITING_LIST_CAN_NOT_BOOK_TOO_MANY_FUTURE": "too_many_future_bookings",
    "OFFER_WAITING_LIST_NO_USABLE_CONSUMER_PAYMENT_PACK": "no_payment_pack",
    "OFFER_WAITING_LIST_LOCKED_BY_PENDING_BOOKINGS": "locked_pending",
    "OFFER_NO_LONGER_CONVERTIBLE": "spot_taken",
}

# Numeric codes from `user_registration`'s `error_codes` pairs. Names are
# the app's own constant names, kept in the comments so a future reader can
# match them back to the bundle.
_BSPORT_NUMERIC_CODE_TO_REASON: dict[int, BookErrorReason] = {
    2014: "booking_limit_reached",   # CONSUMER_PAYMENT_PACK_CAN_NOT_BOOK_MAXOUT_DAY
    2015: "booking_limit_reached",   # ..._MAXOUT_WEEK — the weekly cap
    2016: "booking_limit_reached",   # ..._MAXOUT_MONTH
    2017: "booking_limit_reached",   # ..._MAXOUT_YEAR
    2018: "no_credit",               # ..._ENOUGH_CREDIT
    2019: "pack_disabled",           # ..._DISABLED
    6002: "waitlist_full",           # OFFER_WAITING_LIST_STATUS_FULL
    6003: "already_booked",          # OFFER_WAITING_LIST_STATUS_ALREADY_BOOKED
    6005: "locked_pending",          # OFFER_WAITING_LIST_LOCKED_BY_PENDING_BOOKINGS
    8001: "spot_unavailable",        # SPOT_NOT_AVAILABLE
    23001: "no_payment_pack",        # OFFER_WAITING_LIST_NO_USABLE_CONSUMER_PAYMENT_PACK
    23002: "too_many_future_bookings",  # OFFER_WAITING_LIST_CAN_NOT_BOOK_TOO_MANY_FUTURE
}


def _reason_for_code(bsport_code: str | int | None) -> BookErrorReason:
    """Resolve either code namespace to a normalized reason.

    Numeric codes sometimes arrive as digit strings, so a string that parses
    as an int is tried against the numeric map before falling back to the
    string map.
    """
    if bsport_code is None:
        return "unknown_client_error"
    if isinstance(bsport_code, bool):
        # bool is an int subclass; a boolean here is never a real code.
        return "unknown_client_error"
    if isinstance(bsport_code, int):
        return _BSPORT_NUMERIC_CODE_TO_REASON.get(
            bsport_code, "unknown_client_error"
        )
    try:
        numeric = int(bsport_code)
    except (TypeError, ValueError):
        pass
    else:
        return _BSPORT_NUMERIC_CODE_TO_REASON.get(
            numeric, "unknown_client_error"
        )
    return _BSPORT_CODE_TO_REASON.get(bsport_code, "unknown_client_error")


def normalize_book_error(
    bsport_code: str | int | None,
    *,
    status: int,
    raw_body: str,
    retry_after: str | None = None,
) -> BsportBookError | BsportRateLimited:
    """Map a bsport 4xx/429 response to our exception hierarchy."""
    if status == 429:
        try:
            secs = float(retry_after) if retry_after is not None else 60.0
        except ValueError:
            secs = 60.0
        return BsportRateLimited(retry_after=secs)
    if status == 423:
        return BsportBookError(
            reason="cannot_book", status=status, raw_body=raw_body,
        )
    return BsportBookError(
        reason=_reason_for_code(bsport_code),
        status=status,
        raw_body=raw_body,
    )
