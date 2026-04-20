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


def normalize_book_error(
    bsport_code: str | None,
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
    reason = _BSPORT_CODE_TO_REASON.get(
        bsport_code or "", "unknown_client_error"
    )
    return BsportBookError(reason=reason, status=status, raw_body=raw_body)
