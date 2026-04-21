"""Pure-data dataclasses for the bsport API. HA-agnostic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

WaitlistStatus = Literal[
    "waiting", "opening", "convertible", "already_booked", "expired"
]
WatchStatus = Literal["awaiting_window", "bookable", "booked", "expired"]
BookingStatus = Literal["confirmed", "attended", "cancelled", "noshow"]
MembershipStatus = Literal["active", "suspended", "expired", "cancelled"]


@dataclass(frozen=True, slots=True)
class Offer:
    offer_id: int
    class_name: str
    category: str
    coach: str | None
    start_at: datetime  # UTC, tz-aware
    end_at: datetime
    bookable_at: datetime
    is_bookable_now: bool
    is_waitlist_only: bool
    # Public URL to the activity's cover image (used as entity_picture in HA
    # UI). May be absent when the source response doesn't surface it (e.g.
    # the flat /book/v1/offer/ schedule listing).
    cover_url: str | None = None


@dataclass(frozen=True, slots=True)
class WaitlistEntry:
    entry_id: int            # waitlist-entry id — distinct from offer.offer_id
    offer: Offer
    status: WaitlistStatus
    # Zero-indexed position in the waitlist queue (0 = first).
    # None when we haven't fetched the dedicated position endpoint yet.
    position: int | None
    # Total number of OTHER people waiting on the same offer. Does not
    # include the current user. None when unavailable.
    waiting_list_size: int | None = None
    # Queue type as reported by the API. 1 = dynamic/unordered, 0 = FIFO.
    # None when unavailable.
    dynamic: int | None = None


@dataclass(frozen=True, slots=True)
class WatchedClass:
    offer: Offer
    status: WatchStatus


@dataclass(frozen=True, slots=True)
class Booking:
    booking_id: int          # REQUIRED for cancel endpoint
    offer: Offer
    status: BookingStatus


@dataclass(frozen=True, slots=True)
class Pass:
    pass_id: int
    name: str
    classes_remaining: int | None   # None = unlimited (membership-style pack)
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Membership:
    status: MembershipStatus
    product_name: str
    next_renewal_at: datetime | None


@dataclass(frozen=True, slots=True)
class AccountOverview:
    waitlists: tuple[WaitlistEntry, ...]
    bookings: tuple[Booking, ...]
    active_pass: Pass | None
    membership: Membership | None
