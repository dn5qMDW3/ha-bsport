"""bsport API client — public surface."""
from .client import AccountProfile, BsportClient
from .errors import (
    BsportAuthError,
    BsportBookError,
    BsportError,
    BsportRateLimited,
    BsportTransientError,
    normalize_book_error,
)
from .models import (
    AccountOverview,
    Booking,
    BookingStatus,
    Membership,
    MembershipStatus,
    Offer,
    Pass,
    WaitlistEntry,
    WaitlistStatus,
    WatchedClass,
    WatchStatus,
)

__all__ = [
    "AccountOverview",
    "AccountProfile",
    "Booking",
    "BookingStatus",
    "BsportAuthError",
    "BsportBookError",
    "BsportClient",
    "BsportError",
    "BsportRateLimited",
    "BsportTransientError",
    "Membership",
    "MembershipStatus",
    "Offer",
    "Pass",
    "WaitlistEntry",
    "WaitlistStatus",
    "WatchedClass",
    "WatchStatus",
    "normalize_book_error",
]
