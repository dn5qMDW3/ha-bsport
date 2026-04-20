# bsport booking — Home Assistant integration

Track class waitlists and upcoming-registration openings on the bsport
booking platform, get notified as soon as a spot becomes reachable,
and one-tap (or automatically) book from Home Assistant.

## Features

- Per-account hub device with upcoming-bookings calendar, pass state,
  membership state.
- One device per active waitlist entry — status (`waiting` / `convertible`
  / …), position, and a Book button.
- One device per watched class — watch any class whose registration window
  hasn't opened yet; the integration notifies you when it does.
- Home Assistant events (`bsport_spot_open`, `bsport_class_bookable`,
  `bsport_book_succeeded`, `bsport_book_failed`, `bsport_auth_failed`) —
  the integration is automation-ready; wire your own notification channel.

## Installation

### Via HACS (custom repository)

1. In HACS → *Integrations* → three-dot menu → *Custom repositories*.
2. Add `https://github.com/dn5qMDW3/ha-bsport` as an *Integration*.
3. Install *bsport booking*.
4. Restart Home Assistant.
5. *Settings → Devices & Services → Add integration → bsport*.
6. Pick your studio (Chimosa, Mindful Life Berlin, or enter a custom bsport company id).
7. Enter the email and password you use for that studio's app.

### Multiple accounts

Add the integration multiple times — one config entry per login. Each
entry creates its own hub device and exposes entities under it.

## Entities

| Platform | Entity | Purpose |
|---|---|---|
| `sensor` | `<account>_next_booking` | Start time of your next class |
| `sensor` | `<account>_upcoming_count` | Number of upcoming confirmed classes |
| `calendar` | `<account>_bookings` | All upcoming confirmed bookings |
| `sensor` | `<account>_pass_classes_remaining` | Classes left on your pass |
| `sensor` | `<account>_pass_expires` | Pass expiry |
| `sensor` | `<account>_membership_status` | `active` / `suspended` / `expired` |
| `sensor` | `<account>_membership_renewal` | Next billing date |
| `sensor` | `waitlist_<class>_status` | `waiting` → `convertible` when a spot opens |
| `sensor` | `waitlist_<class>_position` | Position in the waitlist |
| `button` | `waitlist_<class>_book` | One-tap book |
| `sensor` | `watch_<class>_status` | `awaiting_window` → `bookable` |
| `sensor` | `watch_<class>_opens_at` | When registration opens |
| `button` | `watch_<class>_book` | One-tap book once opened |

## Services

| Service | Fields | Effect |
|---|---|---|
| `bsport.book_offer` | `entry_id, offer_id` | Book an offer using an active pack |
| `bsport.cancel_booking` | `entry_id, offer_id` | Cancel a confirmed booking |
| `bsport.watch_class` | `entry_id, offer_id` | Add a class to the watch list |
| `bsport.unwatch_class` | `entry_id, offer_id` | Remove a class from the watch list |
| `bsport.simulate_spot_open` | `entry_id, offer_id` | Fire a fake `bsport_spot_open` event (for testing your notify automation without waiting for a real spot) |

## Automation blueprint

Import `docs/blueprints/bsport-notify-and-book.yaml` to get a notification
with an actionable "Book" button when either `bsport_spot_open` or
`bsport_class_bookable` fires. The notify service is a blueprint input
(defaults to `notify.persistent_notification`) — set it to
`notify.mobile_app_<your_device>`, `notify.telegram_bot`, or any other
`notify.*` service.

### Verifying the notification pipeline

Don't wait for a real waitlist spot to open — test the wiring immediately:

1. *Settings → Devices & Services → bsport → your account* → copy the entry
   id from the URL (`/config/integrations/integration/bsport` shows a
   UUID-looking slug).
2. *Developer Tools → Services* → pick `bsport.simulate_spot_open`.
3. Fill in your `entry_id` and any `offer_id` (use `1` if you don't have a
   real one handy).
4. Press *Call Service*.

If your automation is set up, a notification appears within a second.
The event payload carries `simulated: true`, so production automations
that care can filter those out:

```yaml
- condition: template
  value_template: "{{ not trigger.event.data.get('simulated', False) }}"
```

You can also inspect the event on *Developer Tools → Events → Listen* with
`bsport_spot_open` as the event type, then call the service to see the raw
payload.

## License & attribution

This integration talks to the private bsport HTTPS API with credentials
you already own. It is not affiliated with or endorsed by bsport or any
studio running on the platform. Use at your own risk.
