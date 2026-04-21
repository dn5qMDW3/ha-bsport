<div align="center">

<img src="https://cdn.prod.website-files.com/69b9945989b426c38041ecbc/69b9945989b426c38041edac_Logo%20light.svg" alt="bsport booking logo" width="128">

# bsport booking

**A Home Assistant integration for studios running on the [bsport](https://bsport.io) booking platform.**

Track waitlists, get notified the moment a spot opens, one-tap book from the notification, and surface your upcoming classes, passes, and membership as native HA entities.

Tested with Chimosa and Mindful Life Berlin. Should work with any studio on the bsport platform, if yours isn't on the list, pick **Other** during setup and enter its numeric company id.

[![HACS](https://img.shields.io/badge/HACS-Custom_repository-41BDF5?logo=home-assistant)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.1.0+-03A9F4?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)
[![Python](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![GitHub Release](https://img.shields.io/github/v/release/dn5qMDW3/ha-bsport?include_prereleases&sort=semver)](https://github.com/dn5qMDW3/ha-bsport/releases)

</div>

---

## What you get

| | |
|---|---|
| **Live waitlist polling** | Adaptive cadence: 30 seconds close to class start, 10 minutes when it's days away. Fires `bsport_spot_open` on the HA bus the instant `is_convertible` flips true. |
| **Pre-registration watch** | Mark a class that isn't yet open for booking; the integration polls and fires `bsport_class_bookable` the moment the registration window opens. |
| **One-tap book from notifications** | Ships with a blueprint that sends an actionable notification and books for you when you tap the button. |
| **Upcoming-bookings calendar** | Your confirmed classes as a native HA calendar, drop it on a dashboard or trigger automations with it. |
| **Pass & membership state** | Sensors for classes remaining, pass expiry, membership status, next renewal. |
| **Multi-studio, multi-account** | One config entry per studio + login. Add as many as you want. |
| **Searchable studio picker** | 410+ bsport studios pre-loaded in the config flow with typeahead search; the list is auto-refreshed weekly via a scheduled GitHub Action. |
| **Branded entities** | Per-studio logos and per-class cover images flow through as `entity_picture`, so your HA dashboards look like the studio's own app. |
| **Tested** | 99 unit tests covering API client, parsers, coordinators, entities, config flow, services. |

## Supported studios

The config flow ships with a searchable dropdown of **410+ studios** on the `api.production.bsport.io` platform, auto-discovered from public app-store listings. The list is regenerated weekly by a scheduled GitHub Action ([`discover-studios.yaml`](.github/workflows/discover-studios.yaml)) that opens a PR on change.

Tested with:

- **Chimosa** (Berlin)
- **Mindful Life Berlin**

Your studio uses bsport if its Android package name looks like `com.bsport_<number>`. If it's not in the dropdown, pick **Other** in the config flow and enter the numeric id directly; the integration will work the same way.

## Installation

### via HACS (recommended)

1. Open HACS → **Integrations** → ⋮ menu → **Custom repositories**
2. Add `https://github.com/dn5qMDW3/ha-bsport` as an **Integration**
3. Install **bsport booking**
4. **Restart Home Assistant**
5. **Settings → Devices & Services → Add Integration → bsport booking**
6. Pick your studio → enter email + password

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=bsport)

### Manual installation

```bash
cd /config  # your HA config directory
mkdir -p custom_components
curl -L https://github.com/dn5qMDW3/ha-bsport/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1 ha-bsport-main/custom_components/bsport \
  -C custom_components/
```

Restart HA, then add the integration via the UI as above.

## Entities

<details open>
<summary><b>Account hub</b>, one per config entry</summary>

| Platform | Translation key | Device class | Description |
|---|---|---|---|
| `sensor` | `next_booking` | `timestamp` | Start time of your next confirmed class |
| `sensor` | `upcoming_count` | — | Number of upcoming confirmed bookings |
| `calendar` | `bookings` | — | All upcoming confirmed bookings as a native HA calendar |
| `sensor` | `pass_classes_remaining` | — | Classes left on your active pass |
| `sensor` | `pass_expires` | `timestamp` | When your pass expires |
| `sensor` | `membership_status` | — | `active` / `suspended` / `expired` / `cancelled` |
| `sensor` | `membership_renewal` | `timestamp` | Next billing date |

</details>

<details>
<summary><b>Waitlist entry</b>, one per waitlisted class</summary>

| Platform | Translation key | Description |
|---|---|---|
| `sensor` | `waitlist_status` | `waiting` → `opening` → `convertible` |
| `sensor` | `waitlist_position` | Your position in the queue |
| `button` | `waitlist_book` | One-tap book when `convertible` |

</details>

<details>
<summary><b>Watched class</b>, one per class you've added via the options flow</summary>

| Platform | Translation key | Device class | Description |
|---|---|---|---|
| `sensor` | `watch_status` | — | `awaiting_window` / `bookable` / `booked` / `expired` |
| `sensor` | `watch_opens_at` | `timestamp` | When registration opens |
| `button` | `watch_book` | — | One-tap book once bookable |

</details>

## Services

| Service | Required fields | Effect |
|---|---|---|
| `bsport.book_offer` | `entry_id`, `offer_id` | Book an offer through your active pack |
| `bsport.cancel_booking` | `entry_id`, `offer_id` | Cancel a confirmed booking |
| `bsport.watch_class` | `entry_id`, `offer_id` | Add a class to the watch list |
| `bsport.unwatch_class` | `entry_id`, `offer_id` | Remove from the watch list |
| `bsport.simulate_spot_open` | `entry_id`, `offer_id` | Fire a synthetic `bsport_spot_open` event, useful for testing your notify automation without waiting for a real spot |

## Events on the HA bus

Every event carries `entry_id` so automations on multi-account setups can disambiguate.

| Event | When it fires | Payload keys |
|---|---|---|
| `bsport_spot_open` | A waitlist entry transitions to `convertible` | `entry_id, offer_id, class_name, category, coach, start_at, position_was` |
| `bsport_class_bookable` | A watched class becomes bookable | `entry_id, offer_id, class_name, category, coach, start_at` |
| `bsport_book_succeeded` | `book_offer` returned 2xx | `entry_id, offer_id, class_name, start_at, source` |
| `bsport_book_failed` | `book_offer` failed | `entry_id, offer_id, class_name, reason, source` |
| `bsport_auth_failed` | Token refresh and silent re-auth both failed | `entry_id, email` |

## Automation: notify & one-tap book

The integration ships a **bsport: notify & one-tap book** blueprint that's installed automatically the first time you set up the integration. No manual import step.

To use it: **Settings → Automations & Scenes → Blueprints** → scroll to `bsport/` → **bsport: notify & one-tap book** → *Create automation from blueprint*. Pick your notify service (defaults to `notify.persistent_notification`; swap to `notify.mobile_app_<your_device>`, `notify.telegram_bot`, `notify.ntfy`, etc.) and paste your bsport entry id. That's it, the automation listens for `bsport_spot_open` and `bsport_class_bookable`, fires an actionable notification with a **Book** button, and calls `bsport.book_offer` when you tap it.

The blueprint file is preserved on integration uninstall if you've modified it; if untouched it gets cleaned up so your config directory stays tidy.

### Verifying the pipeline without waiting

```yaml
# Developer Tools → Services
service: bsport.simulate_spot_open
data:
  entry_id: <paste from Settings → Devices & Services URL>
  offer_id: 1
```

A notification appears within a second if your automation is wired correctly. The payload carries `simulated: true`, so production automations can filter synthetic events out:

```yaml
condition: template
value_template: "{{ not trigger.event.data.get('simulated', False) }}"
```

## How it works

<details>
<summary>Under the hood</summary>

- **Auth**: one POST to `/platform/v1/authentication/signin/with-login/` with email + password returns a 40-char DRF auth token used as `Authorization: Token <token>` on every call.
- **Polling topology**: one `AccountOverviewCoordinator` per entry (10 min fixed) fans out to `/api-v0/booking/future/`, `/api-v0/waiting-list/booking-option/`, and `/core-data/v1/membership/`. Per-waitlist and per-watch coordinators have their own adaptive schedules.
- **Adaptive cadence**: waitlist polling tightens to 30 s when the class is under 2 h away, 2 min when under 24 h, 10 min beyond that. Watch polling uses an event-driven schedule anchored to the `bookable_at` timestamp.
- **Book via pack**: `/buyable/v1/payment-pack/consumer-payment-pack/<pack_id>/register_booking/`. Active packs are discovered and tried in order.
- **Cancel**: resolves `offer_id → booking_id` via `/booking/future/`, then `POST /book/v1/booking/<booking_id>/cancel/`.

See [`docs/API_NOTES.md`](docs/API_NOTES.md) for the full knowledge base: every confirmed endpoint, error code, known gotcha, and how to re-run the recon.

</details>

## Requirements

- Home Assistant **2026.1.0** or later
- Python **3.12** or later (ships with HA)

## Contributing

Pull requests welcome. Please run the suite locally before submitting:

```bash
python3 -m venv .venv
.venv/bin/pip install homeassistant==2026.1.0 pytest-homeassistant-custom-component \
                      aiohttp aioresponses flake8
.venv/bin/pytest
.venv/bin/flake8 custom_components tests
```

New studio? Add its `(company_id, "Studio Name")` tuple to [`KNOWN_STUDIOS`](custom_components/bsport/const.py) and open a PR.

## License & attribution

This integration talks to the private bsport HTTPS API with credentials the user already owns. It is not affiliated with, endorsed by, or sponsored by bsport or any studio running on the platform. Use at your own risk.
