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
    """Parse a raw offer dict from /book/v1/offer/ or nested in other endpoints."""
    activity = raw.get("activity") or {}
    coach_dict = activity.get("coach") or {}

    start_at = _parse_dt(raw["date_start"])
    duration = raw.get("duration_minute") or 0
    end_at = start_at + timedelta(minutes=duration)
    bookable_at = start_at - timedelta(days=14)

    available = raw.get("available")
    is_full = raw.get("is_full")
    is_waiting_list_full = raw.get("is_waiting_list_full")

    is_bookable_now = bool(available) and not bool(is_full)
    is_waitlist_only = bool(is_full) and not bool(is_waiting_list_full)

    return Offer(
        offer_id=int(raw["id"]),
        class_name=str(activity.get("name") or ""),
        category=str(activity.get("category") or ""),
        coach=coach_dict.get("name") if coach_dict else None,
        start_at=start_at,
        end_at=end_at,
        bookable_at=bookable_at,
        is_bookable_now=is_bookable_now,
        is_waitlist_only=is_waitlist_only,
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
