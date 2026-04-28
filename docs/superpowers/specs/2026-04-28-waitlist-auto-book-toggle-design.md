# Waitlist auto-book toggle

**Design spec — 2026-04-28**

Adds a per-class binary on/off switch in the Home Assistant UI that auto-books
a waitlisted class the moment a spot becomes reachable — but only if the spot
opens far enough ahead of class start that the user can plan around it. Spots
opening close to class time still surface a notification (via the existing
`bsport_spot_open` event and bundled blueprint) but are not auto-booked, so the
user doesn't get pulled into a class they may miss.

Builds on the existing waitlist coordinator and per-waitlist device pattern —
no new device kinds, no new coordinator kinds.

---

## 1. Goals and non-goals

### Goals

1. A per-waitlist-class binary switch entity, surfaced on the existing
   per-waitlist HA device, default off (opt-in).
2. When the switch is on AND the spot opens at least *N* hours before class
   start, the integration books the spot automatically without user input.
3. When the spot opens within *N* hours of class start, no auto-book happens —
   the existing `bsport_spot_open` event still fires so the user can be
   notified by the bundled blueprint and decide manually.
4. *N* is a single global setting (default 24 h), exposed in the integration's
   options flow.
5. Switch state survives Home Assistant restarts.

### Non-goals

- Per-class lead-time overrides. One global threshold; ship simple, revisit if
  someone asks for it.
- Direct integration-side notifications. The existing event-and-blueprint path
  already covers this.
- Auto-booking watched classes (i.e. registration-window openings). This spec
  is scoped to *waitlist* conversion only. Auto-book on registration-open is a
  natural follow-up but out of scope here.
- A "default on for all new waitlists" preference. Each waitlist starts off;
  the user opts in per class.

---

## 2. UX

For every waitlist a user is on, the corresponding HA device gains a third
control alongside the existing **Book** and **Leave waitlist** buttons:

- **Auto-book** — `switch.bsport_waitlist_autobook_<class_slug>_<when>`,
  default off. Translation key `waitlist_autobook`, label *"Auto-book"*.

The integration's options flow gains a new menu item:

- **Auto-book lead time** — positive integer in hours, default 24, sensible
  upper bound (e.g. 24 × 14 = 336 h / 2 weeks).

When a spot opens (waitlist status flips to `convertible`):

| Switch | Lead time check                       | Result                                  |
| ------ | ------------------------------------- | --------------------------------------- |
| OFF    | n/a                                   | `EVENT_SPOT_OPEN` only (today's behavior). |
| ON     | `(start_at − now) ≥ lead_time`        | `EVENT_SPOT_OPEN` + auto-book attempt.  |
| ON     | `(start_at − now) < lead_time`        | `EVENT_SPOT_OPEN` only (notify, don't book). |

Successful auto-book → existing reconciler retires the waitlist coordinator
and its device; the switch disappears with it. Failed auto-book → switch
stays on, retry next poll (see §5).

---

## 3. Architecture

### Platforms

`switch` is added to `PLATFORMS` in `const.py`. New file `switch.py` mirrors
the existing `button.py` and `sensor.py` shape — platform setup that iterates
`runtime.waitlists` and adds one entity per coordinator, plus exposing
`runtime.add_switch_entities` for mid-life spawning by the reconciler.

### Entity

```
class WaitlistAutoBookSwitch(
    CoordinatorEntity[WaitlistEntryCoordinator],
    SwitchEntity,
    RestoreEntity,
):
    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_autobook"
```

- `unique_id`: `bsport_<entry_id>_waitlist_autobook_<offer_id>`.
- Lives on the same per-waitlist `DeviceInfo` as the Book / Leave buttons and
  status / position sensors.
- `is_on`: returns `coord._auto_book_enabled`.
- `async_added_to_hass`: read RestoreEntity last state; if it was `STATE_ON`,
  set `coord._auto_book_enabled = True` and (if `coord.data is not None`)
  schedule one immediate `coord.async_maybe_auto_book()` call to handle the
  already-convertible-at-boot case.
- `async_turn_on`: set `coord._auto_book_enabled = True`, write state, then
  `await coord.async_maybe_auto_book()`.
- `async_turn_off`: set `coord._auto_book_enabled = False`, write state.

### Coordinator extension

`WaitlistEntryCoordinator` gains:

- Constructor argument `auto_book_lead_time: timedelta` (seeded from
  `entry.options[OPT_AUTO_BOOK_LEAD_TIME]` at construction).
- Instance attribute `_auto_book_enabled: bool = False`.
- Instance attribute `_book_lock: asyncio.Lock` to serialise manual + auto
  bookings.
- Method `async_maybe_auto_book()` — encapsulates the gate logic (see §4).
- `_async_update_data` calls `async_maybe_auto_book()` after determining the
  new status, on every poll where status is `convertible` (not just on the
  transition — see §5 retry behaviour).
- `async_book` is wrapped by the lock so manual + auto can't race.

### Options flow

`config_flow.py` adds a menu item `set_auto_book_lead_time` with a single
integer input. Reuses the existing `_async_reload_entry` listener — changing
the option triggers a reload, after which coordinators are reconstructed with
the new lead time. Switch state persists across the reload via RestoreEntity.

### Constants and events

- New constant `OPT_AUTO_BOOK_LEAD_TIME` (seconds; default `86400`).
- `_BOOK_SOURCES` literal extended: `"waitlist" | "watch" | "service" | "autobook"`.
  Existing `EVENT_BOOK_SUCCEEDED` / `EVENT_BOOK_FAILED` payloads now carry
  `source: "autobook"` for auto-book outcomes; nothing else changes about
  those events.

### Reconciler integration

`_spawn_waitlist_entities` in `__init__.py` adds the switch alongside the
existing sensor + button entities so a waitlist that appears mid-life still
gets the toggle. Symmetric retire on coordinator removal — RestoreEntity
state is dropped with the entity, no residue.

---

## 4. Data flow

**Switch ON (user toggle):**
1. `async_turn_on` → `coord._auto_book_enabled = True` → write state.
2. `await coord.async_maybe_auto_book()` so a currently-convertible spot is
   booked without waiting for the next poll (cadence is up to 30 s near class).

**Switch OFF (user toggle):**
1. `async_turn_off` → `coord._auto_book_enabled = False` → write state.
2. No further action — in-flight book attempts complete; later polls observe
   the OFF state and don't trigger.

**Coordinator poll observes status `convertible`:**
1. Existing transition logic fires `EVENT_SPOT_OPEN` (unchanged).
2. New: `async_maybe_auto_book()` runs after the status update.

**`async_maybe_auto_book()`:**
1. Return if `_auto_book_enabled` is False.
2. Return if status is not `convertible`.
3. Return if `(start_at − now) < _auto_book_lead_time`.
4. Return if `_book_lock.locked()` (a book is already in flight).
5. Otherwise `await async_book(source="autobook")`.

**Successful auto-book:**
- `EVENT_BOOK_SUCCEEDED` fires with `source: "autobook"`.
- `async_book` already calls `async_request_refresh()`; the overview reconciler
  retires the now-empty waitlist entry; device + switch + buttons disappear.

**Failed auto-book:**
- `EVENT_BOOK_FAILED` fires with `source: "autobook"` and the API `reason`.
- Switch stays on. If status is still `convertible` on the next poll,
  `async_maybe_auto_book` runs again and retries.

**Options flow updates lead time:**
- `_async_reload_entry` triggers a reload. Coordinators are reconstructed with
  the new lead time. Switch state is restored from RestoreEntity.

**Restart:**
- Coordinators init with `_auto_book_enabled = False`.
- Switch's `async_added_to_hass` restores state. If restored ON, applies it to
  the coordinator and triggers one immediate `async_maybe_auto_book()` if
  `coord.data is not None`. Already-convertible-at-boot spots are caught.

---

## 5. Error handling and edge cases

- **`BsportTransientError`** during book → already mapped to `UpdateFailed` in
  `_async_update_data`; switch stays on; next poll retries.
- **`BsportBookError`** (functional, e.g. `cannot_book`, `payment_required`) →
  `EVENT_BOOK_FAILED` fires with `source: "autobook"` and the reason; switch
  stays on; next poll retries while status remains `convertible`.
- **`BsportAuthError`** → existing path: `ConfigEntryAuthFailed`, HA prompts
  for re-auth. Switch state survives via RestoreEntity.
- **Concurrent manual + auto book** → `_book_lock` serialises;
  `async_maybe_auto_book` no-ops when the lock is held, so a user tapping
  Book at the same moment as an auto-book trigger doesn't queue a second
  call.
- **Poll-driven retry while still convertible** → `async_maybe_auto_book` is
  invoked on every poll where status is `convertible`, not only on the
  pending → convertible transition. Otherwise a transient failure followed by
  a still-convertible spot would never retry.
- **Coordinator data not yet populated when switch is restored** → restore
  callback checks `coord.data is not None` before calling
  `async_maybe_auto_book`. The next regular poll handles it.
- **Coordinator retired mid-book** → `async_shutdown` waits for the current
  update to finish (DataUpdateCoordinator behaviour). Worst case: one extra
  book attempt against a freshly-cleared entry, returns `cannot_book` —
  already handled by existing failure path.
- **Switch toggled OFF mid-book** → in-flight HTTP call completes (no
  cancellation of an inflight write that may have already been processed
  server-side). Subsequent polls observe OFF and don't retry.
- **Lead time = 0** → effectively "auto-book any time"; supported.
- **Class start time in the past** (rare, schedule glitch) →
  `(start_at − now)` is negative, fails the `≥ lead_time` check, no booking.
  Safe.

---

## 6. Testing

### New `tests/test_switch.py`

1. Switch entity is registered for each waitlist coordinator with the right
   `unique_id` and waitlist `DeviceInfo`.
2. Default off — newly-spawned waitlist gets `is_on == False`,
   `coord._auto_book_enabled == False`.
3. RestoreEntity restores ON — `coord._auto_book_enabled == True` after
   `async_added_to_hass`.
4. Turn ON when status is already `convertible` and lead time satisfied →
   `client.book_offer` called once, `EVENT_BOOK_SUCCEEDED` fires with
   `source: "autobook"`.
5. Turn ON when status is `convertible` but `(start_at − now) < lead_time` →
   `book_offer` not called.
6. Status flips `pending → convertible` on poll, switch ON, lead-time OK →
   `book_offer` called once.
7. Status flips `pending → convertible` on poll, switch OFF → `book_offer`
   not called.
8. `book_offer` raises `BsportBookError("cannot_book")` →
   `EVENT_BOOK_FAILED` fires with `source: "autobook"`, switch still
   `is_on == True`.
9. Following test 8, next poll with status still `convertible` →
   `book_offer` called again (retry path).
10. Manual Book button pressed mid-poll, lock held →
    `async_maybe_auto_book` no-ops on its concurrent attempt; only one
    `book_offer` call total.

### Extending `tests/test_options_flow.py`

11. Lead-time option roundtrip — set via options flow, entry reloads,
    coordinators reinitialised with the new value.
12. Lead-time validation — non-numeric or negative input rejected via the
    standard options-flow error path.

### Extending `tests/test_coordinator_waitlist.py`

13. Update fixtures to pass the new `auto_book_lead_time` argument when
    constructing `WaitlistEntryCoordinator`. Existing assertions unaffected
    (default 24 h doesn't trigger auto-book in the existing test scenarios).

### Translations

14. Add `entity.switch.waitlist_autobook.name` to `translations/en.json` and
    `translations/fr.json`.
15. Add `options.step.set_auto_book_lead_time.*` strings (title, description,
    field label) to both translation files. Add the new menu entry under
    `options.step.init.menu_options`.

---

## 7. Out of scope / follow-ups

- Per-class lead-time override.
- Auto-book on registration-open (i.e. for `WatchedClassCoordinator`). The
  same switch pattern would apply if desired later.
- A "default on for new waitlists" account-level preference.
- Direct push notifications from the integration when a spot opens within
  the cutoff (today the bundled blueprint covers this).
