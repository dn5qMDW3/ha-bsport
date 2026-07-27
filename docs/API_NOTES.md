# bsport API — integration knowledge base

What this integration talks to and why it does what it does. Kept deliberately
short. Raw recon captures and the original APK-analysis script live on the
maintainer's disk (both gitignored). Everything load-bearing for maintenance
should be in this file.

Last updated: 2026-07-27 (re-verified against Chimosa 7.34.1).

---

## Platform shape

- **bsport is a single multi-tenant platform.** Every "studio app" (Chimosa,
  Mindful Life Berlin, …) is a white-label of the same mobile shell. The
  Android package name is `com.bsport_<tenant_id>` — `com.bsport_538` is
  Chimosa, `com.bsport_2387` is Mindful Life Berlin.
- **One API host:** `https://api.production.bsport.io`. Not studio-scoped.
- **Services are path-prefix-versioned** (discovered from
  `https://backoffice.bsport.io/env.js`):
  - `/platform/v1/…` — auth and tenant-global things.
  - `/core-data/v0|v1/…` — user, member, membership.
  - `/book/v0|v1/…` — offers and booking reads/writes.
  - `/buyable/v0|v1/…` — payment packs.
  - `/api-v0/…` — legacy bucket; still holds a lot of consumer endpoints.
  - `/communicate/…`, `/financial-services/…`, `/member-experience/…`,
    `/cdp/…`, `/business-insights/…`, `/staff-management/…` — not used by
    this integration as of now.
- **Per-account = per-studio.** In the platform membership model, each bsport
  account is tied to exactly one studio (verified by logging in against
  Chimosa and MLB separately — both returned `count: 1` on the membership
  endpoint). If one human uses two studios, they have two accounts with
  (typically) distinct credentials.

## Auth

- Single call: `POST /platform/v1/authentication/signin/with-login/` with JSON
  body `{"email": …, "password": …}`.
- Response shape: `{"status", "firebaseToken", "token", "email_confirmed",
  "is_staff", "is_superuser"}`. The 40-char hex `token` is a Django REST
  Framework `authtoken.Token`. The `firebaseToken` is a Firebase custom token
  that the mobile app uses for Firebase services (Firestore chat, FCM push) —
  **not needed for REST access**.
- All subsequent REST calls: `Authorization: Token <token>`.
- **Firebase email/password sign-in is DISABLED on bsport's Firebase
  project** (`PASSWORD_LOGIN_DISABLED`). The integration must not use the
  Firebase auth path — only the direct bsport signin above.
- 403 on signin = wrong credentials. Empty body. The earlier assumption that
  403 was CSRF was wrong; there's no CSRF layer for this endpoint.
- Error: `invalid_auth` if 403, `cannot_connect` if transient.

## Read endpoints used by the integration

| Path | Used for |
|---|---|
| `GET /core-data/v1/member/me/` | User profile. Fields: `first_name, last_name, gender, email, birthday, phone, emergency_contact, address, photo`. |
| `GET /core-data/v1/membership/` | DRF-paginated list. We filter by `company == selected_studio_id` in the config flow. First result gives authoritative `company_name` used as entry title. |
| `GET /api-v0/waiting-list/booking-option/` | **Primary signal.** Array of the user's waitlist entries. The `is_convertible: true` flag is what we poll for and translates into the `bsport_spot_open` event. Supporting fields: `id` (waitlist entry id), `offer` (nested), `cancelled`, `booking` (non-null if auto-converted). |
| `GET /api-v0/booking/future/` | DRF-paginated list of upcoming confirmed bookings. Each result has `id` (the booking id, required for the cancel endpoint), `offer`, `booking_status_code` (0=confirmed, 1=attended, 2=cancelled, 3=noshow), `status` (bool), etc. |
| `GET /book/v1/offer/registered/` | Flat list of `offer_id` integers the user is currently booked into. Fast check. |
| `GET /book/v1/offer/?company=<id>&date=YYYY-MM-DD` | Schedule listing. Supports `activity=<id>` and `date_start__gte=ISO` filters too. DRF-paginated. Used for the "Add watched class" picker and for the `WatchedClassCoordinator` to locate an offer by id. |
| `GET /book/v1/offer/bookable_status_list/?id__in=<csv>` | **Authoritative per-offer booking state**, batched. Response `{"results": [{"id", "bookable_status", "waiting_list_status"}]}` — the app reduces `results` into a `byId` map. `bookable_status`: 0 bookable, 1 window not open yet, 2 window closed, 3 full, 4 locked. `waiting_list_status`: 0 open, 1 full, 2 already booked, 3 convertible. Preferred over inferring bookability from the schedule listing's `available`/`full` flags. |
| `GET /book/v1/offer/waiting_list_position_list/?id__in=<csv>` | Batched queue positions. Per row: `waiting_list_position: {member_position, waiting_list_size, dynamic}`. `dynamic`: 0 = ordered/FIFO, 1 = unordered (the app hides positions entirely when unordered). |
| `GET /buyable/v1/payment-pack/consumer-payment-pack/` | User's payment packs. Returns a plain JSON list (no pagination envelope). Fields per pack: `id, disabled, reverted, starting_date, ending_date, used_credits, available_credits, bookings_this_week, payment_pack_id, ending_date, …`. **Filter active**: `not disabled and not reverted and starting_date <= today <= ending_date`. Subscription members have many pre-provisioned future-month packs — see "Gotchas" below. |

## Write endpoints

| Method + path | Body | Notes |
|---|---|---|
| `POST /api-v0/waiting-list/booking-option/register/` | `{"offer": <offer_id>}` | Adds to waitlist. `201` = success. `423` = idempotent (already on the waitlist). |
| `POST /buyable/v1/payment-pack/consumer-payment-pack/<pack_id>/register_booking/` | `{"offer": <offer_id>}` | Primary book endpoint. `201` returns the updated pack record with the new booking id appended to its `bookings` array. `423` = cap/full/exhausted — map to `BsportBookError(reason="cannot_book")`. |
| `POST /book/v1/booking/<booking_id>/cancel/` | `{}` | Cancels a confirmed booking. Returns the updated booking with `booking_status_code: 2`. **Important: takes booking_id, not offer_id.** Our `cancel_booking(offer_id)` resolves offer_id → booking_id via `/api-v0/booking/future/` first. |

## Error model

- `403` on auth endpoints = invalid credentials. Empty body.
- `403` on data endpoints with a token = token rejected. Map to `BsportAuthError`.
- `423` (Locked) on book = cannot book right now. Empty body. Reasons: weekly
  cap reached, class full, pack exhausted, pack not yet active. All collapse
  to `BsportBookError(reason="cannot_book")`.
- `429` = rate-limited. Read `Retry-After`, pause all subsequent requests via
  the shared `_pause_until` gate on the client (see
  `BsportClient._wait_if_paused`).
- `500` = server-side exception, usually caused by a malformed body (DRF
  serializer fails after partial validation). Retry once on 5xx with 1 s
  backoff, then give up as transient.

## Gotchas

- **Pack list is huge for subscription members.** A user with an annual
  subscription typically has one pack pre-provisioned per upcoming month.
  Our first implementation of `list_active_packs` only rejected
  `ending_date < today`, which let 21 future-month packs through on the
  test account. `book_offer` then wasted 21 × 423 attempts on those before
  reaching today's pack — and occasionally got rate-limited in the process.
  Fix (commit `fd02a0e`): also reject packs where `starting_date > today`,
  sort survivors by `ending_date` ascending so today's pack is tried first.
- **`bookings_this_week` counts cancelled bookings too** (we observed this
  when a same-week book-then-cancel still left the counter incremented).
  Don't treat this field as "available slots remaining".
- **Waitlist conversion does not bypass the weekly cap.** When a waitlist
  entry transitions to `is_convertible: true`, the normal book endpoint
  still returns 423 if the user has hit the weekly cap. There is no
  secret cap-bypass endpoint. The only ways to actually convert a
  waitlist entry at cap: cancel another booking first, or upgrade the
  pack. The integration correctly surfaces this via
  `bsport_book_failed` event with reason `cannot_book`.
- **The app stores strings in a packed Hermes string table without
  separators.** `strings(1)` output concatenates adjacent entries. Use
  `hermes-dec` (installable via `pip install hermes-dec`) to get
  real decompiled JS with proper URL literals. Search the `.js` output for
  `'/path/'` regexes, not the raw bundle bytes.
- **`/core-data/v1/company/<id>/` requires admin/backoffice scope.** Member
  tokens get `403`. No unauthed company-lookup exists — public pages that
  embed a studio widget probably use a JWT issued per-embed.
- **Config flow must keep the user's password.** Firebase refresh tokens
  have finite lifetimes; the integration falls back to silent re-auth with
  the stored password if a token refresh fails. If the user changes their
  bsport password, the entry needs to be re-added.

## `user_registration` — the batched book/waitlist endpoint

`POST /book/v1/offer/user_registration/` is what the app's "Book" button
drives. It handles booking and waitlist registration in one call and answers
200 even on failure — the outcome is in the body, not the status.

Request:

```json
{
  "consumer_payment_pack": 123,
  "offers":      [{"offer_id": "456", "extra_data": {"booking_for_member": null,
                                                     "additional_guest_info": []}}],
  "waiting_list": [{"offer_id": "789", "extra_data": {}}]
}
```

Put an offer in `offers` to book it, in `waiting_list` to join its waitlist.
(An earlier round of recon probed this with the wrong keys and got 200 with
an empty `offers_booked` — that's what a body the server doesn't recognise
looks like, not a broken endpoint.)

Response buckets: `offers_booked`, `offer_on_waiting_list`, `error_codes`,
`extra_data`.

**`error_codes` is a list of `[offer_id, numeric_code]` pairs** — the app
reduces it into `{offer_id: code}`. The numeric codes are a separate
namespace from the string `code` field other endpoints return. The set we
map lives in `api/errors.py`; notable ones:

| Code | Meaning |
|---|---|
| 2014 / 2015 / 2016 / 2017 | pack maxout day / **week** / month / year |
| 2018 | not enough credit |
| 2019 | pack disabled |
| 6002 / 6003 / 6005 | waitlist full / already booked / locked by pending bookings |
| 8001 | spot not available |
| 23001 | no usable consumer payment pack |
| 23002 | too many future bookings |

2015 is the weekly cap — the thing that makes a convertible waitlist entry
fail to convert (see the gotcha above).

## Unresolved / deferred

- **Real registration-window length.** `bookable_status` tells us *whether*
  a class is open, which is what the watcher actually needs. It doesn't say
  *when* it opens. The app computes that as
  `date_start − meta_activity.first_booking_minutes_until`, but
  `meta_activity` is null on both offer shapes we fetch; the app gets it
  from a per-offer retrieve we haven't located. `parse_offer` therefore
  still estimates `bookable_at` as `start_at − 14 days`, now used *only*
  to pick a poll cadence — never to decide bookability.
- **Per-studio waitlist configuration.** `GET
  /book/v1/waiting-list/configuration/<company_id>/` exists and carries at
  least `waiting_list_disabled` and `dynamic`. We read `dynamic` off the
  per-offer position payload instead; the studio-level call would let us
  suppress position sensors wholesale on unordered waitlists.
- **ICS feed.** `GET
  /book/v1/booking/my-calendar-by-token/<auth_token>/booking-feed.ics`
  returns the user's bookings as iCalendar, authenticated by the token in
  the path. Not needed by `calendar.py` (which builds events from the REST
  reads) but worth documenting for users who want a phone-calendar
  subscription.
- **Discard waitlist endpoint.** `discardBookingOption` function exists in
  the mobile bundle but the URL wasn't extracted. `POST
  /api-v0/waiting-list/booking-option/discard/` is a guess worth probing
  if an "unwatch" / "leave waitlist" service is ever needed beyond the
  options-flow remove.

## Running the recon script

The capture script (`scripts/capture_fixtures.py`, gitignored) uses
credentials from a local `.secrets` file and:

1. Signs into bsport, caches the token.
2. Probes a curated list of paths across the known service prefixes.
3. Writes redacted JSON fixtures to `tests/fixtures/` (gitignored) so tests
   can reference them locally.
4. Writes `tests/fixtures/ENDPOINT_MAP.md` mapping every fixture back to the
   URL that produced it.

To add a new studio or extend coverage: populate `.secrets` with the
studio's credentials, edit the `probes` list in the script, rerun.

## Decompiling the Hermes bundle

```bash
# Extract the base APK from the .xapk (both gitignored)
unzip -p Chimosa_7.34.1_APKPure.xapk com.bsport_538.apk > /tmp/chimosa.apk
unzip -p /tmp/chimosa.apk assets/index.android.bundle > /tmp/bundle.hbc

# Decompile with hermes-dec (pip install hermes-dec)
hbc-decompiler /tmp/bundle.hbc /tmp/chimosa_decompiled.js

# Search for URL literals and function names
grep -oE "Original name: [a-zA-Z]+" /tmp/chimosa_decompiled.js | sort -u | grep -i book
grep -oE "'/[a-z_/-]{5,80}'" /tmp/chimosa_decompiled.js | sort -u | grep -i wait
```

The output is ~1.2M lines of low-level register-based pseudocode, but the
string literals and function names are intact — good for finding endpoints
and tracing caller relationships.
