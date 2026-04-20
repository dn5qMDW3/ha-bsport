"""Smoke test for api.models — ensures frozen/slots don't regress."""
from datetime import datetime, timezone

import pytest

from custom_components.bsport.api import models


def _offer() -> models.Offer:
    return models.Offer(
        offer_id=1, class_name="Pilates", category="Pilates", coach="Léa",
        start_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        bookable_at=datetime(2026, 4, 17, 18, 0, tzinfo=timezone.utc),
        is_bookable_now=True, is_waitlist_only=False,
    )


def test_offer_is_frozen():
    o = _offer()
    with pytest.raises((AttributeError, Exception)):
        o.offer_id = 2  # type: ignore[misc]


def test_offer_uses_slots():
    o = _offer()
    with pytest.raises((AttributeError, TypeError)):
        o.extra_attribute = "nope"  # type: ignore[attr-defined]


def test_booking_has_booking_id():
    b = models.Booking(booking_id=42, offer=_offer(), status="confirmed")
    assert b.booking_id == 42
    assert b.status == "confirmed"


def test_waitlist_entry_has_entry_id():
    w = models.WaitlistEntry(
        entry_id=6529922, offer=_offer(), status="waiting", position=3,
    )
    assert w.entry_id == 6529922
