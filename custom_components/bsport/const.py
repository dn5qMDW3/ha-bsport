"""Constants for the bsport integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "bsport"

# URLs
BSPORT_API_BASE: Final = "https://api.production.bsport.io"
BSPORT_SIGNIN_URL: Final = f"{BSPORT_API_BASE}/platform/v1/authentication/signin/with-login/"

# Scan intervals
OVERVIEW_SCAN_INTERVAL: Final = timedelta(minutes=10)

WAITLIST_INTERVAL_BEYOND_24H: Final = timedelta(minutes=10)
WAITLIST_INTERVAL_WITHIN_24H: Final = timedelta(minutes=2)
WAITLIST_INTERVAL_WITHIN_2H: Final = timedelta(seconds=30)

WATCH_PRE_WINDOW_FAR: Final = timedelta(hours=24)
WATCH_PRE_WINDOW_MID: Final = timedelta(minutes=5)
WATCH_PRE_WINDOW_NEAR: Final = timedelta(seconds=60)
WATCH_PRE_WINDOW_IMMINENT: Final = timedelta(seconds=5)
WATCH_POST_OPEN: Final = timedelta(minutes=5)

# Jitter (0–10% randomised delay on every interval).
SCAN_JITTER_RATIO: Final = 0.10

# HA event names
EVENT_SPOT_OPEN: Final = "bsport_spot_open"
EVENT_CLASS_BOOKABLE: Final = "bsport_class_bookable"
EVENT_BOOK_SUCCEEDED: Final = "bsport_book_succeeded"
EVENT_BOOK_FAILED: Final = "bsport_book_failed"
EVENT_AUTH_FAILED: Final = "bsport_auth_failed"

# Config entry keys (entry.data)
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_BSPORT_TOKEN: Final = "bsport_token"
CONF_BSPORT_USER_ID: Final = "bsport_user_id"
CONF_STUDIO_ID: Final = "studio_id"
CONF_STUDIO_NAME: Final = "studio_name"

# Config entry options
OPT_WATCHED_OFFER_IDS: Final = "watched_offer_ids"

PLATFORMS: Final = ["sensor", "button", "calendar"]
