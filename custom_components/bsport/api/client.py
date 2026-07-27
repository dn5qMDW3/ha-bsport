"""bsport REST client. HA-agnostic — depends only on aiohttp + const."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

from ..const import BSPORT_API_BASE, BSPORT_SIGNIN_URL
from .errors import BsportAuthError, BsportBookError, BsportRateLimited, BsportTransientError, normalize_book_error
from .models import AccountOverview, Booking, Offer, OfferStatus, WaitlistEntry
from .parsers import parse_booking, parse_membership, parse_offer, parse_waitlist_entry


def _extract_error_code(error_codes: object) -> str | int | None:
    """Pull the first usable code out of a `user_registration` error payload.

    The shape the app itself decodes is a list of ``[offer_id, code]`` pairs
    — it maps each entry through ``slice(pair, 2)`` and reduces them into
    ``{offer_id: code}``. That's the case we care about most, because
    missing it collapses every distinguishable failure (weekly cap, no
    credit, pack disabled) into `unknown_client_error`.

    bsport has also shipped a bare list of strings and a list of dicts keyed
    by `code`, so those are still handled. We don't rely on a fixed schema —
    just take the first usable token.
    """
    if not isinstance(error_codes, list) or not error_codes:
        return None
    first = error_codes[0]
    if isinstance(first, (str, int)) and not isinstance(first, bool):
        return first
    if isinstance(first, dict):
        for key in ("code", "error_code", "reason"):
            val = first.get(key)
            if isinstance(val, (str, int)) and not isinstance(val, bool) and val:
                return val
    if isinstance(first, (list, tuple)):
        # `[offer_id, code]` — the code is the second element. A one-element
        # entry is treated as the code itself rather than dropped.
        code = first[1] if len(first) >= 2 else (first[0] if first else None)
        if isinstance(code, (str, int)) and not isinstance(code, bool):
            return code
    return None


@dataclass(frozen=True, slots=True)
class AccountProfile:
    bsport_token: str
    bsport_user_id: int
    studio_id: int
    studio_name: str
    # Public URL to the studio's branding image (company_cover field from
    # /core-data/v1/membership/). None when the studio doesn't have one
    # configured. The config flow stashes this in entry.data so hub-device
    # entities can reference it as entity_picture.
    studio_cover: str | None = None


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
        cover = matching.get("company_cover")
        return AccountProfile(
            bsport_token=self._token,
            bsport_user_id=int(matching["user_id"]),
            studio_id=int(matching["company"]),
            studio_name=str(matching["company_name"]),
            studio_cover=str(cover) if isinstance(cover, str) and cover else None,
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

    async def list_waitlists_with_positions(
        self,
    ) -> tuple[WaitlistEntry, ...]:
        """Return every waitlist entry with its queue position in 2 HTTP calls.

        Replaces the N×2 per-offer fan-out the coordinators used before:
        one call to `/api-v0/waiting-list/booking-option/` for the list, one
        call to `/book/v1/offer/waiting_list_position_list/?id__in=<ids>` for
        all positions. Empty list → empty result, no second request.
        """
        await self._wait_if_paused()
        list_url = self._bsport_url("/api-v0/waiting-list/booking-option/")
        list_body = await self._get_json(list_url)
        raw_list = list_body if isinstance(list_body, list) else []
        if not raw_list:
            return ()

        entries = [parse_waitlist_entry(raw) for raw in raw_list]
        ids = ",".join(str(e.offer.offer_id) for e in entries)
        pos_url = self._bsport_url(
            f"/book/v1/offer/waiting_list_position_list/?id__in={ids}"
        )
        positions: dict[int, tuple[int | None, int | None, int | None]] = {}
        try:
            pos_body = await self._get_json(pos_url)
        except BsportTransientError:
            # Positions are decoration; missing them just leaves the fields
            # None. The convertible transition detection works regardless.
            pos_body = None
        if isinstance(pos_body, dict):
            for row in pos_body.get("results") or []:
                oid = int(row.get("id", 0)) if isinstance(row, dict) else 0
                wlp = (row or {}).get("waiting_list_position") or {}
                if not isinstance(wlp, dict):
                    continue
                mp = wlp.get("member_position") if isinstance(wlp.get("member_position"), int) else None
                ws = wlp.get("waiting_list_size") if isinstance(wlp.get("waiting_list_size"), int) else None
                dy = wlp.get("dynamic") if isinstance(wlp.get("dynamic"), int) else None
                positions[oid] = (mp, ws, dy)

        from dataclasses import replace
        out: list[WaitlistEntry] = []
        for e in entries:
            mp, ws, dy = positions.get(e.offer.offer_id, (None, None, None))
            out.append(
                replace(e, position=mp, waiting_list_size=ws, dynamic=dy)
            )
        return tuple(out)

    async def list_bookable_status(
        self, offer_ids: tuple[int, ...] | list[int]
    ) -> dict[int, OfferStatus]:
        """Return authoritative booking state for *offer_ids* in one call.

        `GET /book/v1/offer/bookable_status_list/?id__in=<csv>` — the same
        endpoint the mobile app drives its "Book" button from. It folds the
        studio's registration window, capacity and per-member locks into a
        single `bookable_status` code, which is strictly better than
        inferring bookability from the schedule listing's `available` /
        `full` flags plus a guessed 14-day window.

        Missing offers are simply absent from the returned mapping. A
        transient failure raises; callers that treat status as decoration
        should catch BsportTransientError and fall back.
        """
        if not offer_ids:
            return {}
        await self._wait_if_paused()
        ids = ",".join(str(int(o)) for o in offer_ids)
        url = self._bsport_url(
            f"/book/v1/offer/bookable_status_list/?id__in={ids}"
        )
        body = await self._get_json(url)
        # The app reduces `results` into `byId` keyed on each record's `id`.
        # Accept a bare list too, in case this endpoint ever drops the
        # pagination envelope the way the payment-pack list does.
        if isinstance(body, dict):
            rows = body.get("results") or []
        elif isinstance(body, list):
            rows = body
        else:
            rows = []

        out: dict[int, OfferStatus] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_id = row.get("id")
            if not isinstance(raw_id, int) or isinstance(raw_id, bool):
                continue
            bookable = row.get("bookable_status")
            waiting = row.get("waiting_list_status")
            out[raw_id] = OfferStatus(
                offer_id=raw_id,
                bookable_status=(
                    bookable
                    if isinstance(bookable, int)
                    and not isinstance(bookable, bool)
                    else None
                ),
                waiting_list_status=(
                    waiting
                    if isinstance(waiting, int)
                    and not isinstance(waiting, bool)
                    else None
                ),
            )
        return out

    async def _compatible_packs_for_offer(self, offer_id: int) -> list[dict]:
        """Return payment packs that can book *offer_id*, in server-preferred order.

        Uses bsport's `compatible_with_offer_unfiltered/?mine=true` endpoint,
        which mirrors what the mobile app does — the server already knows
        which of the user's packs are usable for this offer (no expired /
        future-reserved / disabled), so we avoid the "try each pack until one
        sticks" loop the integration used before.
        """
        await self._wait_if_paused()
        url = self._bsport_url(
            "/buyable/v1/payment-pack/consumer-payment-pack/"
            "compatible_with_offer_unfiltered/?mine=true"
        )
        status, text, body = await self._post_json(
            url, json_body={"offer": offer_id}
        )
        if status != 200:
            raise BsportTransientError(
                f"compatible-packs lookup failed: HTTP {status}"
            )
        if not isinstance(body, list):
            return []
        return body

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

        Uses `/book/v1/offer/user_registration/` — the same endpoint the
        mobile app uses for both direct booking and waitlist joining. The
        server routes the offer into `offer_on_waiting_list` when the class
        is full (expected case) or `offers_booked` if a spot was available.
        Either outcome is success from our POV: the member ends up in a
        valid queue or a real booking.
        """
        await self._wait_if_paused()
        url = self._bsport_url("/book/v1/offer/user_registration/")
        status, text, body = await self._post_json(
            url,
            json_body={
                "waiting_list": [
                    {"offer_id": offer_id, "extra_data": {}},
                ],
            },
        )
        if status == 200 and isinstance(body, dict):
            waitlisted = body.get("offer_on_waiting_list") or []
            booked = body.get("offers_booked") or []
            if offer_id in waitlisted or offer_id in booked:
                return None
            error_codes = body.get("error_codes") or []
            if not error_codes:
                # Nothing placed, nothing reported — treat as already-waitlisted.
                # bsport's response to a duplicate register is undocumented;
                # this is the least-surprising interpretation.
                return None
            code = _extract_error_code(error_codes)
            raise normalize_book_error(code, status=status, raw_body=text)
        bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
        raise normalize_book_error(bsport_code, status=status, raw_body=text)

    async def discard_waitlist(self, waitlist_entry_id: int) -> None:
        """Remove the authenticated user from the waitlist entry.

        `waitlist_entry_id` is the `id` field on a `/waiting-list/booking-option/`
        record — not the offer id. 200 → success. 404 → treated as idempotent
        success (entry already gone).
        """
        await self._wait_if_paused()
        url = self._bsport_url(
            f"/api-v0/waiting-list/booking-option/{waitlist_entry_id}/discard/"
        )
        status, text, body = await self._post_json(url, json_body={})
        if status in (200, 204, 404):
            return None
        bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
        raise normalize_book_error(bsport_code, status=status, raw_body=text)

    async def book_offer(self, offer_id: int) -> Booking:
        """Book *offer_id* via the mobile-app booking endpoint.

        Mirrors the mobile client flow:

        1. Ask the server which of the user's packs are compatible with
           the offer (handles expiry, reservation, pack-type rules).
        2. POST `/book/v1/offer/user_registration/` with the first pack +
           the offer in `offers` (not `waiting_list`). The server places
           the offer into one of three response buckets:
             * `offers_booked`          → real booking, we're done
             * `offer_on_waiting_list`  → class was full, user got a
                                           waitlist entry instead; surface
                                           as `cannot_book` so callers can
                                           distinguish this from a success
             * `error_codes`            → pack rejected / quota hit / etc.

        Raises BsportBookError(reason="no_payment_pack") if no compatible pack.
        Raises BsportBookError(reason="cannot_book") if the class was full.
        Retries once (1s sleep) on a 5xx from the booking endpoint.
        """
        await self._wait_if_paused()
        packs = await self._compatible_packs_for_offer(offer_id)
        if not packs:
            raise BsportBookError(reason="no_payment_pack", status=0, raw_body="")

        pack_id = int(packs[0]["id"])
        url = self._bsport_url("/book/v1/offer/user_registration/")
        body_json = {
            "consumer_payment_pack": pack_id,
            "offers": [
                {
                    "offer_id": str(offer_id),
                    "extra_data": {
                        "booking_for_member": None,
                        "additional_guest_info": [],
                    },
                }
            ],
            "waiting_list": [],
        }
        status, text, body = await self._post_json(url, json_body=body_json)
        if 500 <= status < 600:
            await asyncio.sleep(1)
            status, text, body = await self._post_json(url, json_body=body_json)

        if status == 200 and isinstance(body, dict):
            booked = body.get("offers_booked") or []
            waitlisted = body.get("offer_on_waiting_list") or []
            error_codes = body.get("error_codes") or []
            if offer_id in booked:
                return await self._resolve_new_booking(offer_id)
            if offer_id in waitlisted:
                # Class was full; user ended up on the waitlist. That's not
                # a book success — surface it so the caller can decide.
                raise BsportBookError(
                    reason="cannot_book", status=423, raw_body=text,
                )
            if error_codes:
                code = _extract_error_code(error_codes)
                raise normalize_book_error(code, status=status, raw_body=text)
            raise BsportTransientError(
                f"user_registration returned no booking/waitlist/error for {offer_id}"
            )

        bsport_code = (body or {}).get("code") if isinstance(body, dict) else None
        raise normalize_book_error(bsport_code, status=status, raw_body=text)

    async def _resolve_new_booking(self, offer_id: int) -> Booking:
        """Look up the just-created booking by offer_id via /booking/future/."""
        future_url = self._bsport_url("/api-v0/booking/future/")
        future_body = await self._get_json(future_url)
        results = (
            future_body.get("results", [])
            if isinstance(future_body, dict)
            else []
        )
        for raw in results:
            offer = raw.get("offer") or {}
            if int(offer.get("id", -1)) == offer_id:
                return parse_booking(raw)
        raise BsportTransientError(
            f"booking confirmed but offer {offer_id} not yet in /booking/future/"
        )

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
