"""bsport REST client. HA-agnostic — depends only on aiohttp + const."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import aiohttp

from ..const import BSPORT_API_BASE, BSPORT_SIGNIN_URL
from .errors import BsportAuthError, BsportTransientError
from .models import AccountOverview, Offer, WaitlistEntry
from .parsers import parse_booking, parse_membership, parse_offer, parse_waitlist_entry


@dataclass(frozen=True, slots=True)
class AccountProfile:
    bsport_token: str
    bsport_user_id: int
    studio_id: int
    studio_name: str


class BsportClient:
    """Top-level API client. Owns the DRF authtoken and bsport HTTP calls."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ):
        self._http = session
        self._email = email
        self._password = password
        self._token: str | None = None

    async def authenticate(self) -> None:
        """Sign in and cache the DRF authtoken."""
        try:
            async with self._http.post(
                BSPORT_SIGNIN_URL,
                json={"email": self._email, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 403:
                    raise BsportAuthError(
                        "bsport rejected credentials (HTTP 403)"
                    )
                if 500 <= resp.status < 600:
                    raise BsportTransientError(
                        f"bsport signin: HTTP {resp.status}"
                    )
                if resp.status != 200:
                    raise BsportAuthError(
                        f"bsport signin: unexpected HTTP {resp.status}"
                    )
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BsportTransientError(f"bsport signin: {err}") from err

        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise BsportAuthError("bsport signin did not return a token")
        self._token = token

    async def authenticate_and_fetch_profile(self) -> AccountProfile:
        """Sign in, then read the user's studio affiliation."""
        await self.authenticate()
        try:
            async with self._http.get(
                self._bsport_url("/core-data/v1/membership/"),
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401 or resp.status == 403:
                    raise BsportAuthError(
                        f"bsport rejected token on /membership/ (HTTP {resp.status})"
                    )
                if 500 <= resp.status < 600:
                    raise BsportTransientError(
                        f"bsport membership: HTTP {resp.status}"
                    )
                if resp.status != 200:
                    raise BsportAuthError(
                        f"bsport membership: unexpected HTTP {resp.status}"
                    )
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BsportTransientError(f"bsport membership: {err}") from err

        results = (body or {}).get("results") or []
        if not results:
            raise BsportAuthError(
                "no membership found for this account — user must have at "
                "least one studio affiliation"
            )
        first = results[0]
        assert self._token is not None
        return AccountProfile(
            bsport_token=self._token,
            bsport_user_id=int(first["user_id"]),
            studio_id=int(first["company"]),
            studio_name=str(first["company_name"]),
        )

    def _auth_headers(self) -> dict[str, str]:
        assert self._token is not None, "call authenticate() first"
        return {"Authorization": f"Token {self._token}"}

    def _bsport_url(self, path: str) -> str:
        return f"{BSPORT_API_BASE}{path}"

    async def _get_json(self, url: str) -> object:
        """GET url with auth headers; raise on 401/403/5xx/network errors."""
        try:
            async with self._http.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
                    raise BsportAuthError(
                        f"bsport rejected token (HTTP {resp.status}) at {url}"
                    )
                if 500 <= resp.status < 600:
                    raise BsportTransientError(
                        f"bsport server error: HTTP {resp.status} at {url}"
                    )
                if resp.status != 200:
                    raise BsportTransientError(
                        f"bsport unexpected HTTP {resp.status} at {url}"
                    )
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BsportTransientError(f"bsport network error at {url}: {err}") from err

    async def get_account_overview(self) -> AccountOverview:
        """Fan out to 3 endpoints concurrently and compose an AccountOverview."""
        waitlist_url = self._bsport_url("/api-v0/waiting-list/booking-option/")
        bookings_url = self._bsport_url("/api-v0/booking/future/")
        membership_url = self._bsport_url("/core-data/v1/membership/")

        waitlist_data, bookings_data, membership_data = await asyncio.gather(
            self._get_json(waitlist_url),
            self._get_json(bookings_url),
            self._get_json(membership_url),
        )

        waitlists = tuple(
            parse_waitlist_entry(entry)
            for entry in (waitlist_data if isinstance(waitlist_data, list) else [])
        )
        bookings_results = (
            bookings_data.get("results", [])
            if isinstance(bookings_data, dict)
            else []
        )
        bookings = tuple(parse_booking(b) for b in bookings_results)

        membership_results = (
            membership_data.get("results", [])
            if isinstance(membership_data, dict)
            else []
        )
        membership = parse_membership(membership_results[0]) if membership_results else None

        return AccountOverview(
            waitlists=waitlists,
            bookings=bookings,
            active_pass=None,
            membership=membership,
        )

    async def list_upcoming_offers(
        self,
        *,
        company: int,
        date: str | None = None,
        activity: int | None = None,
    ) -> tuple[Offer, ...]:
        """Return first page of upcoming offers for a company."""
        params: list[str] = [f"company={company}"]
        if date is not None:
            params.append(f"date={date}")
        if activity is not None:
            params.append(f"activity={activity}")
        query = "&".join(params)
        url = self._bsport_url(f"/book/v1/offer/?{query}")
        body = await self._get_json(url)
        results = body.get("results", []) if isinstance(body, dict) else []
        return tuple(parse_offer(r) for r in results)

    async def get_waitlist_entry(self, offer_id: int) -> WaitlistEntry | None:
        """Return the waitlist entry for the given offer, or None if not found."""
        url = self._bsport_url("/api-v0/waiting-list/booking-option/")
        body = await self._get_json(url)
        entries = body if isinstance(body, list) else []
        for entry in entries:
            if (entry.get("offer") or {}).get("id") == offer_id:
                return parse_waitlist_entry(entry)
        return None

    async def list_active_packs(self) -> tuple[dict, ...]:
        """Return active (non-disabled, non-reverted, non-expired) packs sorted by ending_date desc."""
        url = self._bsport_url("/buyable/v1/payment-pack/consumer-payment-pack/")
        body = await self._get_json(url)
        results: list[dict] = body.get("results", []) if isinstance(body, dict) else []

        today = date.today().isoformat()

        def _is_active(pack: dict) -> bool:
            if pack.get("disabled") or pack.get("reverted"):
                return False
            ending = pack.get("ending_date")
            if ending is not None and ending < today:
                return False
            return True

        active = [p for p in results if _is_active(p)]
        active.sort(key=lambda p: p.get("ending_date") or "", reverse=True)
        return tuple(active)
