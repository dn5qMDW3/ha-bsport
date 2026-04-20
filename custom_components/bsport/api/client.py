"""bsport REST client. HA-agnostic — depends only on aiohttp + const."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import aiohttp

from ..const import BSPORT_API_BASE, BSPORT_SIGNIN_URL
from .errors import BsportAuthError, BsportBookError, BsportRateLimited, BsportTransientError, normalize_book_error
from .models import AccountOverview, Booking, Offer, WaitlistEntry
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
        self._pause_until: datetime | None = None

    async def _wait_if_paused(self) -> None:
        if self._pause_until is None:
            return
        remaining = (
            self._pause_until - datetime.now(timezone.utc)
        ).total_seconds()
        if remaining > 0:
            await asyncio.sleep(remaining)
        self._pause_until = None

    def _set_rate_limit(self, retry_after: float) -> None:
        self._pause_until = datetime.now(timezone.utc) + timedelta(
            seconds=retry_after
        )

    async def authenticate(self) -> None:
        """Sign in and cache the DRF authtoken."""
        await self._wait_if_paused()
        try:
            async with self._http.post(
                BSPORT_SIGNIN_URL,
                json={"email": self._email, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 60))
                    self._set_rate_limit(retry_after)
                    raise BsportRateLimited(retry_after=retry_after)
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

    async def authenticate_and_fetch_profile(
        self, studio_id: int
    ) -> AccountProfile:
        """Sign in, then verify the user belongs to `studio_id` and return its metadata."""
        await self.authenticate()
        try:
            async with self._http.get(
                self._bsport_url("/core-data/v1/membership/"),
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (401, 403):
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
        matching = next(
            (r for r in results if int(r.get("company", -1)) == int(studio_id)),
            None,
        )
        if matching is None:
            raise BsportAuthError(
                f"account not a member of studio {studio_id}; memberships: "
                f"{[(r.get('company'), r.get('company_name')) for r in results]}"
            )
        assert self._token is not None
        return AccountProfile(
            bsport_token=self._token,
            bsport_user_id=int(matching["user_id"]),
            studio_id=int(matching["company"]),
            studio_name=str(matching["company_name"]),
        )

    def _auth_headers(self) -> dict[str, str]:
        assert self._token is not None, "call authenticate() first"
        return {"Authorization": f"Token {self._token}"}

    def _bsport_url(self, path: str) -> str:
        return f"{BSPORT_API_BASE}{path}"

    async def _get_json(self, url: str) -> object:
        """GET url with auth headers; raise on 401/403/429/5xx/network errors."""
        await self._wait_if_paused()
        try:
            async with self._http.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 60))
                    self._set_rate_limit(retry_after)
                    raise BsportRateLimited(retry_after=retry_after)
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
        await self._wait_if_paused()
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
        await self._wait_if_paused()
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
        await self._wait_if_paused()
        url = self._bsport_url("/api-v0/waiting-list/booking-option/")
        body = await self._get_json(url)
        entries = body if isinstance(body, list) else []
        for entry in entries:
            if (entry.get("offer") or {}).get("id") == offer_id:
                return parse_waitlist_entry(entry)
        return None

    async def list_active_packs(self) -> tuple[dict, ...]:
        """Return active (non-disabled, non-reverted, non-expired) packs sorted by ending_date desc."""
        await self._wait_if_paused()
        url = self._bsport_url("/buyable/v1/payment-pack/consumer-payment-pack/")
        body = await self._get_json(url)
        if isinstance(body, list):
            results: list[dict] = body
        else:
            results = body.get("results", []) if isinstance(body, dict) else []

        today = date.today().isoformat()

        def _is_active(pack: dict) -> bool:
            if pack.get("disabled") or pack.get("reverted"):
                return False
            # Reject packs reserved for future months — they return 423 if we
            # try to book through them. bsport often pre-provisions a pack per
            # upcoming month for subscription members.
            starting = pack.get("starting_date")
            if starting is not None and starting > today:
                return False
            ending = pack.get("ending_date")
            if ending is not None and ending < today:
                return False
            return True

        active = [p for p in results if _is_active(p)]
        # Sort by ending_date ascending — use the pack that expires soonest
        # first, so upcoming-month packs don't sit unused while today's pack
        # accumulates the weekly cap.
        active.sort(key=lambda p: p.get("ending_date") or "")
        return tuple(active)

    async def _post_json(
        self,
        url: str,
        *,
        json_body: dict,
        timeout_secs: float = 15,
    ) -> tuple[int, str, dict | None]:
        """POST url with auth headers and JSON body.

        Returns (status, text, parsed_body_or_None).
        Raises BsportAuthError on 401/403, BsportRateLimited on 429,
        BsportTransientError on network errors.
        Does NOT raise on 5xx or other 4xx — callers handle those.
        """
        await self._wait_if_paused()
        try:
            async with self._http.post(
                url,
                json=json_body,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=timeout_secs),
            ) as resp:
                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", 60))
                    self._set_rate_limit(retry_after)
                    raise BsportRateLimited(retry_after=retry_after)
                if resp.status in (401, 403):
                    raise BsportAuthError(
                        f"bsport rejected token (HTTP {resp.status}) at {url}"
                    )
                text = await resp.text()
                try:
                    body: dict | None = await resp.json(content_type=None)
                except Exception:
                    body = None
                return resp.status, text, body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BsportTransientError(f"bsport network error at {url}: {err}") from err

    async def register_waitlist(self, offer_id: int) -> None:
        """Register the authenticated user on the waitlist for *offer_id*.

        201 → success.
        423 → already on the waitlist; treated as idempotent success.
        Other 4xx → raises BsportBookError via normalize_book_error.
        """
        await self._wait_if_paused()
        url = self._bsport_url("/api-v0/waiting-list/booking-option/register/")
        status, text, body = await self._post_json(url, json_body={"offer": offer_id})
        if status in (201, 423):
            return None
        bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
        raise normalize_book_error(bsport_code, status=status, raw_body=text)

    async def book_offer(self, offer_id: int) -> Booking:
        """Book *offer_id* using the first available active payment pack.

        Raises BsportBookError(reason="no_payment_pack") if no active packs.
        Raises BsportBookError(reason="cannot_book", status=423) if all packs
        are locked (423).
        Retries once (with 1 s sleep) on a 5xx from any pack endpoint.
        """
        await self._wait_if_paused()
        packs = await self.list_active_packs()
        if not packs:
            raise BsportBookError(reason="no_payment_pack", status=0, raw_body="")

        last_status = 0
        for pack in packs:
            pack_id = pack["id"]
            url = self._bsport_url(
                f"/buyable/v1/payment-pack/consumer-payment-pack/{pack_id}/register_booking/"
            )
            status, text, body = await self._post_json(url, json_body={"offer": offer_id})

            # Retry once on 5xx (spec §7.2)
            if 500 <= status < 600:
                await asyncio.sleep(1)
                status, text, body = await self._post_json(url, json_body={"offer": offer_id})

            if status == 201:
                # Parse the new booking id from the response bookings array
                bookings_list = (body or {}).get("bookings", []) if isinstance(body, dict) else []
                if not bookings_list:
                    raise BsportTransientError(
                        "book_offer: 201 response contained no bookings array"
                    )
                new_booking_id = int(bookings_list[-1]["id"])
                # Re-fetch /booking/future/ and return the matching Booking
                future_url = self._bsport_url("/api-v0/booking/future/")
                future_body = await self._get_json(future_url)
                results = (
                    future_body.get("results", [])
                    if isinstance(future_body, dict)
                    else []
                )
                for raw in results:
                    if int(raw.get("id", -1)) == new_booking_id:
                        return parse_booking(raw)
                raise BsportTransientError(
                    "booking confirmed but not yet in /booking/future/"
                )

            if status == 423:
                last_status = 423
                continue

            # Other 4xx
            bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
            raise normalize_book_error(bsport_code, status=status, raw_body=text)

        # All packs returned 423
        raise BsportBookError(reason="cannot_book", status=last_status, raw_body="")

    async def cancel_booking(self, offer_id: int) -> None:
        """Cancel the booking for *offer_id*.

        Resolves offer_id → booking_id via /api-v0/booking/future/, then
        POSTs to the cancel endpoint.
        Raises BsportBookError(reason="unknown_client_error") if no matching booking.
        """
        await self._wait_if_paused()
        future_url = self._bsport_url("/api-v0/booking/future/")
        future_body = await self._get_json(future_url)
        results = (
            future_body.get("results", []) if isinstance(future_body, dict) else []
        )

        booking_id: int | None = None
        for raw in results:
            offer = raw.get("offer") or {}
            if int(offer.get("id", -1)) == offer_id:
                booking_id = int(raw["id"])
                break

        if booking_id is None:
            raise BsportBookError(
                reason="unknown_client_error",
                status=0,
                raw_body=f"no booking for offer {offer_id}",
            )

        cancel_url = self._bsport_url(f"/book/v1/booking/{booking_id}/cancel/")
        status, text, body = await self._post_json(cancel_url, json_body={})
        if status == 200:
            return None
        bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
        raise normalize_book_error(bsport_code, status=status, raw_body=text)
