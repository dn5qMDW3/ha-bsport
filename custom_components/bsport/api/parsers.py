"""Pure functions turning bsport API JSON into our dataclasses."""
from __future__ import annotations

from datetime import datetime, timedelta

from .models import Booking, BookingStatus, Membership, Offer, WaitlistEntry, WaitlistStatus


def _parse_dt(s: str) -> datetime:
    """Parse ISO8601 with timezone offset: '2026-04-20T18:30:00+02:00' or '...Z'."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def parse_offer(raw: dict) -> Offer:
    """Parse a raw offer dict from either of the two shapes bsport returns.

    The `/api-v0/waiting-list/booking-option/` and `/api-v0/booking/future/`
    endpoints return a *nested* shape — `activity` is a dict with `name`,
    `category`, `coach`, etc. inside.

    The `/book/v1/offer/?company=X&date=Y` schedule endpoint returns a *flat*
    shape — `activity` is just the integer activity id, `activity_name` lives
    at the top level, and some flags are renamed (`full` instead of `is_full`).

    This function detects the shape from `activity`'s type and reads the
    right fields either way.
    """
    activity = raw.get("activity")
    if isinstance(activity, dict):
        # Nested shape — read from the activity subdocument.
        class_name = str(activity.get("name") or "")
        category = str(activity.get("category") or "")
        coach_obj = activity.get("coach") or {}
        coach_name = (
            coach_obj.get("name")
            if isinstance(coach_obj, dict) and coach_obj
            else None
        )
        # Prefer the smaller thumbnail for HA entity pictures — it renders
        # faster and scales well at sensor-row sizes. Fall back to the main
        # cover if only that's available.
        cover_url: str | None = (
            activity.get("cover_thumbnail")
            or activity.get("cover_main")
            or None
        )
    else:
        # Flat shape from /book/v1/offer/ — activity is an int id.
        class_name = str(raw.get("activity_name") or "")
        category = ""  # not surfaced at the top level
        coach_name = None  # top-level `coach` is an int id, not a name
        # The flat schedule response doesn't carry cover URLs at the top
        # level. The watched-class coordinator accepts `cover_url=None` and
        # HA falls back to the entity's device_class icon.
        cover_url = None

    start_at = _parse_dt(raw["date_start"])
    duration = raw.get("duration_minute") or 0
    end_at = start_at + timedelta(minutes=duration)
    bookable_at = start_at - timedelta(days=14)

    available = raw.get("available")
    # The flat shape uses `full`; the nested shape uses `is_full`.
    is_full = raw.get("is_full")
    if is_full is None:
        is_full = raw.get("full")
    is_waiting_list_full = raw.get("is_waiting_list_full")

    is_bookable_now = bool(available) and not bool(is_full)
    is_waitlist_only = bool(is_full) and not bool(is_waiting_list_full)

    return Offer(
        offer_id=int(raw["id"]),
        class_name=class_name,
        category=category,
        coach=coach_name,
        start_at=start_at,
        end_at=end_at,
        bookable_at=bookable_at,
        is_bookable_now=is_bookable_now,
        is_waitlist_only=is_waitlist_only,
        cover_url=cover_url,
    )


def parse_waitlist_entry(raw: dict) -> WaitlistEntry:
    """Parse a raw waitlist entry from /api-v0/waiting-list/booking-option/."""
    cancelled: bool = bool(raw.get("cancelled"))
    booking = raw.get("booking")
    is_convertible: bool = bool(raw.get("is_convertible"))

    if cancelled:
        status: WaitlistStatus = "expired"
    elif booking is not None:
        status = "already_booked"
    elif is_convertible:
        status = "convertible"
    else:
        status = "waiting"

    return WaitlistEntry(
        entry_id=int(raw["id"]),
        offer=parse_offer(raw["offer"]),
        status=status,
        position=None,
    )


def parse_booking(raw: dict) -> Booking:
    """Parse a raw booking from /api-v0/booking/future/."""
    code = raw.get("booking_status_code", 0)

    if code == 2:
        status: BookingStatus = "cancelled"
    elif code == 3:
        status = "noshow"
    elif code == 1:
        status = "attended"
    else:
        status = "confirmed"

    return Booking(
        booking_id=int(raw["id"]),
        offer=parse_offer(raw["offer"]),
        status=status,
    )


def parse_membership(raw: dict) -> Membership:
    """Parse a raw membership record from /core-data/v1/membership/."""
    product_name = raw.get("name") or raw.get("company_name") or "Membership"
    return Membership(
        status="active",
        product_name=str(product_name),
        next_renewal_at=None,
    )
