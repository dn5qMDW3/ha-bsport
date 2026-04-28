# Waitlist Auto-Book Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-waitlist-class binary switch in the HA UI that auto-books a class when its waitlist spot becomes reachable, gated by a global "minimum lead time" option (default 24 h) so spots opening close to class start are notified about but not auto-booked.

**Architecture:** New `switch` platform with one `WaitlistAutoBookSwitch` per existing waitlist device. The switch is a thin UI surface that flips a flag on the existing `WaitlistEntryCoordinator`; the coordinator owns the gate logic (status convertible AND `(start_at − now) ≥ lead_time` AND not concurrently booking) and calls its existing `async_book`. State persists across restarts via `RestoreEntity`. Lead time is a config-entry option, settable via a new options-flow menu item.

**Tech Stack:** Home Assistant custom_components Python integration; existing patterns — `DataUpdateCoordinator`, `CoordinatorEntity`, `RestoreEntity`, voluptuous schemas, `pytest_homeassistant_custom_component` fixtures.

**Spec:** `docs/superpowers/specs/2026-04-28-waitlist-auto-book-toggle-design.md`

---

## File map

**Create:**
- `custom_components/bsport/switch.py` — switch platform, `WaitlistAutoBookSwitch` entity.
- `tests/test_switch.py` — switch-entity behaviour tests.

**Modify:**
- `custom_components/bsport/const.py` — add `OPT_AUTO_BOOK_LEAD_TIME`, `DEFAULT_AUTO_BOOK_LEAD_TIME`, `MAX_AUTO_BOOK_LEAD_TIME_HOURS`, `BOOK_SOURCE_AUTOBOOK` (string), `"switch"` in `PLATFORMS`.
- `custom_components/bsport/coordinator_waitlist.py` — `_auto_book_enabled`, `_auto_book_lead_time`, `_book_lock`, `async_maybe_auto_book`, lock-wrap `async_book`, extend the `source` `Literal`, call `async_maybe_auto_book` from `_async_update_data`.
- `custom_components/bsport/__init__.py` — `BsportRuntimeData.add_switch_entities`, pass `auto_book_lead_time` when constructing coordinators in `_reconcile_child_coordinators`, spawn switch entity in `_spawn_waitlist_entities`.
- `custom_components/bsport/config_flow.py` — `async_step_set_auto_book_lead_time`, add menu option.
- `custom_components/bsport/translations/en.json` and `translations/fr.json` — switch label, options-flow strings.
- `tests/test_coordinator_waitlist.py` — pass `auto_book_lead_time` in fixtures (only the constructor sites) and add coordinator-level auto-book tests.
- `tests/test_options_flow.py` — set-lead-time roundtrip and validation.
- `custom_components/bsport/manifest.json` — bump version to `1.0.15`.

---

## Conventions used by existing code (reference for the implementer)

- Tests use `pytest.mark.asyncio` and the `hass` fixture from `pytest_homeassistant_custom_component`. Top-level `tests/conftest.py` auto-enables custom integrations.
- Existing per-class entities live on a `_waitlist_device` (defined in `sensor.py`) so the new switch joins that same device — no new device kind.
- The coordinator's `_async_update_data` is the central poll loop, and that's where transition events fire today. `async_maybe_auto_book` runs at the end of every successful update where status is convertible.
- The reconciler in `__init__.py` already creates entities mid-life; `_spawn_waitlist_entities` is where new platforms are wired up.
- `entry.options` are plain dicts; `_async_reload_entry` already triggers a reload on options change.

---

## Task 1: Add constants and switch platform key

**Files:**
- Modify: `custom_components/bsport/const.py:48-51`

- [ ] **Step 1: Add the new constants and platform**

Edit `custom_components/bsport/const.py`. Find the block after `OPT_WATCHED_OFFER_IDS: Final = "watched_offer_ids"` (around line 49) and replace the surrounding section with:

```python
# Config entry options
OPT_WATCHED_OFFER_IDS: Final = "watched_offer_ids"
# Minimum lead time (seconds) before class start at which auto-book triggers.
# When a waitlist spot opens at less than this distance to start, the
# integration emits the spot-open event but does NOT auto-book — the user is
# expected to claim it manually if they want it. Stored as seconds for
# arithmetic with timedelta; the options flow inputs hours.
OPT_AUTO_BOOK_LEAD_TIME: Final = "auto_book_lead_time"
DEFAULT_AUTO_BOOK_LEAD_TIME: Final = timedelta(hours=24)
# Cap to 14 days — the bsport schedule horizon. Larger values would let users
# set effectively-infinite lead times by accident; reject in the options flow.
MAX_AUTO_BOOK_LEAD_TIME_HOURS: Final = 24 * 14

# Tag carried in EVENT_BOOK_SUCCEEDED / EVENT_BOOK_FAILED `source` field for
# bookings the integration triggered automatically (vs the user pressing the
# Book button or invoking a service).
BOOK_SOURCE_AUTOBOOK: Final = "autobook"

PLATFORMS: Final = ["sensor", "button", "calendar", "switch"]
```

- [ ] **Step 2: Verify constants compile**

Run: `.venv/bin/python -c "from custom_components.bsport import const; print(const.OPT_AUTO_BOOK_LEAD_TIME, const.DEFAULT_AUTO_BOOK_LEAD_TIME, const.MAX_AUTO_BOOK_LEAD_TIME_HOURS, const.BOOK_SOURCE_AUTOBOOK, const.PLATFORMS)"`

Expected: prints `auto_book_lead_time 1 day, 0:00:00 336 autobook ['sensor', 'button', 'calendar', 'switch']`.

- [ ] **Step 3: Commit**

```bash
git add custom_components/bsport/const.py
git commit -m "feat(const): add auto-book option keys and switch platform"
```

---

## Task 2: Coordinator gains lead-time and enabled flag (no behaviour yet)

Add the construction-time arguments and instance attributes only. Behaviour wiring lives in Task 3.

**Files:**
- Modify: `custom_components/bsport/coordinator_waitlist.py:107-127`

- [ ] **Step 1: Write a failing test for the new constructor params**

Append this to `tests/test_coordinator_waitlist.py`:

```python
# ── auto-book wiring ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_coordinator_init_accepts_auto_book_lead_time(
    hass: HomeAssistant,
):
    """Constructor takes a lead-time timedelta and stores it; default is 24h."""
    client = AsyncMock(spec=BsportClient)
    initial = _entry(timedelta(hours=3))

    coord_default = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
    )
    assert coord_default._auto_book_lead_time == timedelta(hours=24)
    assert coord_default._auto_book_enabled is False

    coord_custom = WaitlistEntryCoordinator(
        hass, client, "e1", initial=initial,
        batch_cache=_fake_batch(initial),
        auto_book_lead_time=timedelta(hours=2),
    )
    assert coord_custom._auto_book_lead_time == timedelta(hours=2)
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py::test_coordinator_init_accepts_auto_book_lead_time -xvs`

Expected: FAIL — `WaitlistEntryCoordinator.__init__()` rejects `auto_book_lead_time` and/or attribute does not exist.

- [ ] **Step 3: Add the constructor params and attributes**

Edit `custom_components/bsport/coordinator_waitlist.py`. Replace the `__init__` (currently lines 110-127) with:

```python
    def __init__(
        self,
        hass: HomeAssistant,
        client: BsportClient,
        entry_id: str,
        initial: WaitlistEntry,
        batch_cache: WaitlistBatchCache,
        *,
        auto_book_lead_time: timedelta | None = None,
    ):
        self._initial = initial
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_waitlist_{initial.offer.offer_id}",
            update_interval=_select_cadence(initial.offer.start_at),
        )
        self._client = client
        self._batch = batch_cache
        self.entry_id = entry_id
        # Auto-book gate. The switch entity flips _auto_book_enabled; lead time
        # is seeded from the entry options at coordinator construction so a
        # change requires an entry reload (which _async_reload_entry already
        # triggers on options change).
        self._auto_book_enabled: bool = False
        self._auto_book_lead_time: timedelta = (
            auto_book_lead_time
            if auto_book_lead_time is not None
            else timedelta(hours=24)
        )
        # Serialises manual + auto bookings on this coordinator. async_book
        # acquires it; async_maybe_auto_book skips when held.
        self._book_lock: asyncio.Lock = asyncio.Lock()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py::test_coordinator_init_accepts_auto_book_lead_time -xvs`

Expected: PASS.

- [ ] **Step 5: Run the full coordinator test file to ensure no regression**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bsport/coordinator_waitlist.py tests/test_coordinator_waitlist.py
git commit -m "feat(coordinator): accept auto_book_lead_time on waitlist coord"
```

---

## Task 3: Coordinator gains `async_maybe_auto_book` and lock-wraps `async_book`

The book attempt is gated by status, lead time, enabled flag, and the in-flight lock. `async_book` itself acquires the lock so manual + auto can't race; `async_maybe_auto_book` checks the lock without blocking.

**Files:**
- Modify: `custom_components/bsport/coordinator_waitlist.py:169-219` (`async_book`), append new `async_maybe_auto_book`.

- [ ] **Step 1: Write failing tests for the gate logic**

Append to `tests/test_coordinator_waitlist.py`:

```python
@pytest.mark.asyncio
async def test_maybe_auto_book_skips_when_disabled(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    client.book_offer = AsyncMock()
    convertible = _entry(timedelta(hours=3), status="convertible", position=1)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
    )
    coord.data = convertible
    # _auto_book_enabled defaults to False
    await coord.async_maybe_auto_book()
    client.book_offer.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_auto_book_skips_when_status_not_convertible(
    hass: HomeAssistant,
):
    client = AsyncMock(spec=BsportClient)
    client.book_offer = AsyncMock()
    waiting = _entry(timedelta(hours=3), status="waiting", position=3)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=waiting,
        batch_cache=_fake_batch(waiting),
    )
    coord.data = waiting
    coord._auto_book_enabled = True
    await coord.async_maybe_auto_book()
    client.book_offer.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_auto_book_skips_inside_lead_time(hass: HomeAssistant):
    """Spot opens 30 min before class with 24h lead time → no auto-book."""
    client = AsyncMock(spec=BsportClient)
    client.book_offer = AsyncMock()
    convertible = _entry(
        timedelta(minutes=30), status="convertible", position=1
    )
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
        auto_book_lead_time=timedelta(hours=24),
    )
    coord.data = convertible
    coord._auto_book_enabled = True
    await coord.async_maybe_auto_book()
    client.book_offer.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_auto_book_books_when_all_conditions_met(
    hass: HomeAssistant,
):
    client = AsyncMock(spec=BsportClient)
    convertible = _entry(timedelta(days=2), status="convertible", position=1)
    booking = Booking(
        booking_id=42, offer=convertible.offer, status="confirmed",
    )
    client.book_offer = AsyncMock(return_value=booking)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
        auto_book_lead_time=timedelta(hours=24),
    )
    coord.data = convertible
    coord._auto_book_enabled = True

    succeeded: list = []
    hass.bus.async_listen(
        "bsport_book_succeeded", lambda e: succeeded.append(e)
    )

    await coord.async_maybe_auto_book()
    await hass.async_block_till_done()

    assert client.book_offer.await_count == 1
    assert len(succeeded) == 1
    assert succeeded[0].data["source"] == "autobook"


@pytest.mark.asyncio
async def test_maybe_auto_book_skips_when_book_in_flight(
    hass: HomeAssistant,
):
    """If the lock is held (manual book in flight), auto-book no-ops."""
    client = AsyncMock(spec=BsportClient)
    client.book_offer = AsyncMock()
    convertible = _entry(timedelta(days=2), status="convertible", position=1)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
        auto_book_lead_time=timedelta(hours=24),
    )
    coord.data = convertible
    coord._auto_book_enabled = True

    await coord._book_lock.acquire()
    try:
        await coord.async_maybe_auto_book()
    finally:
        coord._book_lock.release()

    client.book_offer.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs -k maybe_auto_book`

Expected: FAIL — `async_maybe_auto_book` does not exist.

- [ ] **Step 3: Implement `async_maybe_auto_book` and lock-wrap `async_book`**

In `custom_components/bsport/coordinator_waitlist.py`:

3a. Update the `Literal` on `async_book` (currently line 170) and lock-wrap the method body. Replace the existing `async_book` method (lines 169-219) with:

```python
    async def async_book(
        self,
        *,
        source: Literal["waitlist", "watch", "service", "autobook"],
    ) -> None:
        """Attempt to book the waitlist offer.

        bsport has no documented "convert waitlist → booking" endpoint. When
        the spot is convertible, the class is still marked full on the
        schedule because the open spot is held by the waitlist reservation,
        so `book_offer` returns 423 `cannot_book`. Workaround: when the
        coordinator sees we're in `convertible` state, drop the waitlist
        entry to release the held spot, then retry the book. Only runs once;
        a second failure is reported as-is.

        Serialised by `_book_lock` so manual presses and auto-book triggers
        can't queue duplicate requests.
        """
        async with self._book_lock:
            entry = self.data if self.data is not None else self._initial
            offer = entry.offer
            offer_id = offer.offer_id
            try:
                await self._client.book_offer(offer_id)
            except BsportBookError as err:
                can_retry = (
                    err.reason == "cannot_book"
                    and self.data is not None
                    and self.data.status == "convertible"
                )
                if can_retry:
                    try:
                        await self._client.discard_waitlist(entry.entry_id)
                    except BsportBookError:
                        # Couldn't drop the reservation — surface the original
                        # book error rather than hide it behind a new one.
                        self._fire_book_failed(offer, source, err.reason)
                        raise err
                    try:
                        await self._client.book_offer(offer_id)
                    except BsportBookError as err2:
                        self._fire_book_failed(offer, source, err2.reason)
                        raise
                else:
                    self._fire_book_failed(offer, source, err.reason)
                    raise
            self.hass.bus.async_fire(
                EVENT_BOOK_SUCCEEDED,
                {
                    "entry_id": self.entry_id,
                    "offer_id": offer_id,
                    "class_name": offer.class_name,
                    "start_at": offer.start_at.isoformat(),
                    "source": source,
                },
            )
            await self.async_request_refresh()
```

3b. Append a new method right after `async_book` and before `_fire_book_failed`:

```python
    async def async_maybe_auto_book(self) -> None:
        """Trigger an auto-book if all gates pass; otherwise no-op.

        Called from the poll loop on every update where status is convertible
        (so a transient failure followed by a still-open spot retries on the
        next poll), and from `WaitlistAutoBookSwitch.async_turn_on` so a
        currently-convertible spot is grabbed without waiting for the next
        poll cycle. Failures are not propagated — they're already surfaced
        via EVENT_BOOK_FAILED, and we don't want to break the poll cycle.
        """
        if not self._auto_book_enabled:
            return
        data = self.data
        if data is None or data.status != "convertible":
            return
        delta = data.offer.start_at - datetime.now(timezone.utc)
        if delta < self._auto_book_lead_time:
            return
        if self._book_lock.locked():
            return
        try:
            await self.async_book(source="autobook")
        except BsportBookError:
            # Already fired EVENT_BOOK_FAILED in async_book; swallow so the
            # poll cycle isn't aborted by a functional booking failure.
            pass
        except BsportTransientError:
            # Same — transient errors are observable via failed events / logs.
            pass
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs -k maybe_auto_book`

Expected: all 5 PASS.

- [ ] **Step 5: Run full coordinator test file**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs`

Expected: all PASS (existing tests still work because lock is acquired, and the source `Literal` is widened, not narrowed).

- [ ] **Step 6: Commit**

```bash
git add custom_components/bsport/coordinator_waitlist.py tests/test_coordinator_waitlist.py
git commit -m "feat(coordinator): async_maybe_auto_book gate logic and book lock"
```

---

## Task 4: Wire `async_maybe_auto_book` into the poll loop

When `_async_update_data` returns the new entry, call `async_maybe_auto_book`. Note the design decision: call it on every poll where status is convertible, not only on the transition — this enables retries when a previous auto-book failed but the spot remains open.

**Files:**
- Modify: `custom_components/bsport/coordinator_waitlist.py:129-167`

- [ ] **Step 1: Write a failing test for poll-loop integration**

Append to `tests/test_coordinator_waitlist.py`:

```python
@pytest.mark.asyncio
async def test_poll_loop_triggers_auto_book_on_convertible(
    hass: HomeAssistant,
):
    """When poll observes status = convertible and conditions are met,
    auto-book runs as part of the same update cycle."""
    client = AsyncMock(spec=BsportClient)
    convertible = _entry(timedelta(days=2), status="convertible", position=1)
    booking = Booking(
        booking_id=42, offer=convertible.offer, status="confirmed",
    )
    client.book_offer = AsyncMock(return_value=booking)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
        auto_book_lead_time=timedelta(hours=24),
    )
    # No prior data — first poll observes convertible directly.
    coord._auto_book_enabled = True

    succeeded: list = []
    hass.bus.async_listen(
        "bsport_book_succeeded", lambda e: succeeded.append(e),
    )

    await coord._async_update_data()
    await hass.async_block_till_done()

    assert client.book_offer.await_count == 1
    assert len(succeeded) == 1
    assert succeeded[0].data["source"] == "autobook"


@pytest.mark.asyncio
async def test_poll_loop_does_not_trigger_when_disabled(hass: HomeAssistant):
    client = AsyncMock(spec=BsportClient)
    client.book_offer = AsyncMock()
    convertible = _entry(timedelta(days=2), status="convertible", position=1)
    coord = WaitlistEntryCoordinator(
        hass, client, "e1", initial=convertible,
        batch_cache=_fake_batch(convertible),
        auto_book_lead_time=timedelta(hours=24),
    )
    # _auto_book_enabled left at default False
    await coord._async_update_data()
    await hass.async_block_till_done()
    client.book_offer.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs -k "poll_loop_triggers_auto_book or poll_loop_does_not_trigger_when_disabled"`

Expected: the first test FAILS (book not called); the second PASSES already.

- [ ] **Step 3: Wire `async_maybe_auto_book` into `_async_update_data`**

In `custom_components/bsport/coordinator_waitlist.py`, replace the `_async_update_data` method (lines 129-167) with:

```python
    async def _async_update_data(self) -> WaitlistEntry:
        try:
            new_entry = await self._batch.get_entry(
                self._initial.offer.offer_id
            )
        except BsportAuthError as err:
            self.hass.bus.async_fire(
                EVENT_AUTH_FAILED,
                {"entry_id": self.entry_id, "email": self._client_email()},
            )
            raise ConfigEntryAuthFailed(str(err)) from err
        except BsportTransientError as err:
            raise UpdateFailed(str(err)) from err

        if new_entry is None:
            raise UpdateFailed("waitlist entry disappeared")

        previous = self.data
        if (
            previous is not None
            and previous.status != "convertible"
            and new_entry.status == "convertible"
        ):
            offer = new_entry.offer
            self.hass.bus.async_fire(
                EVENT_SPOT_OPEN,
                {
                    "entry_id": self.entry_id,
                    "offer_id": offer.offer_id,
                    "class_name": offer.class_name,
                    "category": offer.category,
                    "coach": offer.coach,
                    "start_at": offer.start_at.isoformat(),
                    "position_was": previous.position,
                },
            )

        self.update_interval = _select_cadence(new_entry.offer.start_at)
        # Set self.data so async_maybe_auto_book sees the freshly-observed
        # entry. DataUpdateCoordinator normally assigns self.data after
        # _async_update_data returns; assigning here is safe and allows the
        # auto-book gate to evaluate against the new state without an extra
        # update round-trip. The framework reassigning to the same value is
        # a no-op.
        self.data = new_entry
        if new_entry.status == "convertible":
            await self.async_maybe_auto_book()
        return new_entry
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs -k "poll_loop_triggers_auto_book or poll_loop_does_not_trigger_when_disabled"`

Expected: both PASS.

- [ ] **Step 5: Run the full coordinator test file**

Run: `.venv/bin/python -m pytest tests/test_coordinator_waitlist.py -xvs`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bsport/coordinator_waitlist.py tests/test_coordinator_waitlist.py
git commit -m "feat(coordinator): trigger auto-book from poll loop"
```

---

## Task 5: Plumb lead-time and switch-platform callback through `__init__.py`

**Files:**
- Modify: `custom_components/bsport/__init__.py:36-46` (`BsportRuntimeData`), `:307-321` (reconcile), `:355-380` (`_spawn_waitlist_entities`).

- [ ] **Step 1: Add `add_switch_entities` to `BsportRuntimeData`**

Edit `custom_components/bsport/__init__.py`. Replace the `BsportRuntimeData` dataclass (lines 35-45) with:

```python
@dataclass
class BsportRuntimeData:
    client: BsportClient
    overview: AccountOverviewCoordinator
    waitlist_cache: WaitlistBatchCache
    waitlists: dict[int, WaitlistEntryCoordinator] = field(default_factory=dict)
    watches: dict[int, WatchedClassCoordinator] = field(default_factory=dict)
    # Platform async_add_entities callbacks, captured during platform setup so
    # the reconciler can create entities for coordinators spawned mid-life.
    add_sensor_entities: AddEntitiesCallback | None = None
    add_button_entities: AddEntitiesCallback | None = None
    add_switch_entities: AddEntitiesCallback | None = None
```

- [ ] **Step 2: Pass lead-time to coordinator construction**

In the same file, locate the waitlist construction in `_reconcile_child_coordinators` (around lines 307-321). Add a helper near the top of the function and pass it through. Replace the function body up to the watch section (lines 292-323) with:

```python
async def _reconcile_child_coordinators(
    hass: HomeAssistant, entry: BsportConfigEntry
) -> None:
    """Spawn / retire per-waitlist and per-watch coordinators."""
    runtime = entry.runtime_data
    overview = runtime.overview.data
    if overview is None:
        return

    # Lead time is sourced from options; the entry-reload listener
    # reconstructs coordinators when the user changes it.
    lead_time_seconds = entry.options.get(
        OPT_AUTO_BOOK_LEAD_TIME,
        int(DEFAULT_AUTO_BOOK_LEAD_TIME.total_seconds()),
    )
    auto_book_lead_time = timedelta(seconds=int(lead_time_seconds))

    # Waitlist coordinators
    live_ids = {w.offer.offer_id for w in overview.waitlists}
    for dead_id in list(runtime.waitlists):
        if dead_id not in live_ids:
            await runtime.waitlists.pop(dead_id).async_shutdown()
            _remove_child_device(hass, entry, "waitlist", dead_id)
    for entry_obj in overview.waitlists:
        oid = entry_obj.offer.offer_id
        if oid not in runtime.waitlists:
            coord = WaitlistEntryCoordinator(
                hass, runtime.client, entry_id=entry.entry_id,
                initial=entry_obj,
                batch_cache=runtime.waitlist_cache,
                auto_book_lead_time=auto_book_lead_time,
            )
            await coord.async_refresh()
            runtime.waitlists[oid] = coord
            _spawn_waitlist_entities(runtime, entry, coord, entry_obj.offer.class_name)
```

Also add the new imports near the top of `__init__.py`. Replace the import block at lines 20-27 with:

```python
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_ID,
    DEFAULT_AUTO_BOOK_LEAD_TIME,
    DOMAIN,
    OPT_AUTO_BOOK_LEAD_TIME,
    OPT_WATCHED_OFFER_IDS,
    PLATFORMS,
)
```

And add `from datetime import timedelta` near the top of the file if it isn't there already (check the existing imports — if `timedelta` is missing, add it after line 5: `from datetime import timedelta`).

- [ ] **Step 3: Spawn switch entity in `_spawn_waitlist_entities`**

Replace `_spawn_waitlist_entities` (lines 355-380) with:

```python
def _spawn_waitlist_entities(
    runtime: BsportRuntimeData,
    entry: BsportConfigEntry,
    coord: WaitlistEntryCoordinator,
    class_name: str,
) -> None:
    """Add sensor/button/switch entities for a waitlist coord spawned mid-life.

    No-op during SETUP_IN_PROGRESS: platform setup hasn't run yet, so the
    callbacks are None; the platform will pick the coord up by iterating
    runtime.waitlists when it does run. Late imports avoid a circular dep
    (sensor/button/switch import from this module).
    """
    offer_id = coord._initial.offer.offer_id  # noqa: SLF001
    if runtime.add_sensor_entities is not None:
        from .sensor import WaitlistPositionSensor, WaitlistStatusSensor
        runtime.add_sensor_entities([
            WaitlistStatusSensor(coord, entry, offer_id, class_name),
            WaitlistPositionSensor(coord, entry, offer_id, class_name),
        ])
    if runtime.add_button_entities is not None:
        from .button import WaitlistBookButton, WaitlistDiscardButton
        runtime.add_button_entities([
            WaitlistBookButton(coord, entry),
            WaitlistDiscardButton(coord, entry),
        ])
    if runtime.add_switch_entities is not None:
        from .switch import WaitlistAutoBookSwitch
        runtime.add_switch_entities([
            WaitlistAutoBookSwitch(coord, entry),
        ])
```

- [ ] **Step 4: Verify the module still imports**

Run: `.venv/bin/python -c "from custom_components.bsport import _reconcile_child_coordinators, _spawn_waitlist_entities, BsportRuntimeData; print('ok')"`

Expected: prints `ok` (the `from .switch import ...` lines are inside functions, so the missing module won't break import yet).

- [ ] **Step 5: Run the full test suite to catch any regression**

Run: `.venv/bin/python -m pytest tests/ -x`

Expected: all PASS. (Tests that build coordinators directly are unaffected; entry-setup tests still work because `add_switch_entities` is optional and switch.py is created in the next task.)

- [ ] **Step 6: Commit**

```bash
git add custom_components/bsport/__init__.py
git commit -m "feat(init): pass auto_book_lead_time and wire switch platform"
```

---

## Task 6: Create `switch.py` platform

The switch entity is a thin UI surface: it reflects `coord._auto_book_enabled`, mutates it on toggle, persists state via `RestoreEntity`, and triggers an immediate auto-book check on turn-on.

**Files:**
- Create: `custom_components/bsport/switch.py`

- [ ] **Step 1: Create the switch platform file**

Write `custom_components/bsport/switch.py`:

```python
"""Switch platform for bsport — auto-book toggles."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BsportConfigEntry
from .const import DOMAIN
from .coordinator_waitlist import WaitlistEntryCoordinator
from .sensor import _waitlist_device

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BsportConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up bsport switches from a config entry."""
    runtime = entry.runtime_data
    entities: list[SwitchEntity] = []
    for coord in runtime.waitlists.values():
        entities.append(WaitlistAutoBookSwitch(coord, entry))
    async_add_entities(entities)
    # Expose so the reconciler can spawn per-child switches mid-life.
    runtime.add_switch_entities = async_add_entities


class WaitlistAutoBookSwitch(
    CoordinatorEntity[WaitlistEntryCoordinator], SwitchEntity, RestoreEntity,
):
    """Per-waitlist auto-book toggle.

    The switch is a thin UI mirror of `coord._auto_book_enabled`. The
    coordinator owns the gate logic (status convertible, lead time, lock) and
    the actual book call; this entity only sets the flag and persists state
    via RestoreEntity so a restart preserves the user's choice.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "waitlist_autobook"

    def __init__(
        self, coord: WaitlistEntryCoordinator, entry: BsportConfigEntry,
    ) -> None:
        super().__init__(coord)
        offer = (
            coord.data.offer if coord.data else coord._initial.offer  # noqa: SLF001
        )
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_waitlist_autobook_{offer.offer_id}"
        )
        self._attr_device_info = _waitlist_device(
            entry, offer.offer_id, offer.class_name, offer.start_at,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator._auto_book_enabled  # noqa: SLF001

    @property
    def entity_picture(self) -> str | None:
        data = self.coordinator.data
        return data.offer.cover_url if data else None

    async def async_added_to_hass(self) -> None:
        """Restore state on startup. If restored ON, apply to coordinator
        and trigger an immediate auto-book check for the
        already-convertible-at-boot edge case."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            self.coordinator._auto_book_enabled = True  # noqa: SLF001
            if self.coordinator.data is not None:
                await self.coordinator.async_maybe_auto_book()

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator._auto_book_enabled = True  # noqa: SLF001
        self.async_write_ha_state()
        await self.coordinator.async_maybe_auto_book()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator._auto_book_enabled = False  # noqa: SLF001
        self.async_write_ha_state()
```

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "from custom_components.bsport.switch import WaitlistAutoBookSwitch, async_setup_entry; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add custom_components/bsport/switch.py
git commit -m "feat(switch): add WaitlistAutoBookSwitch platform"
```

---

## Task 7: Switch tests — entity registration, toggle behaviour, restore, retry

**Files:**
- Create: `tests/test_switch.py`

- [ ] **Step 1: Write the test file**

Write `tests/test_switch.py`:

```python
"""Switch entity tests for bsport auto-book toggle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bsport.api import (
    AccountOverview, Booking, BsportBookError, Offer, WaitlistEntry,
)
from custom_components.bsport.const import (
    CONF_EMAIL, CONF_PASSWORD, CONF_STUDIO_ID, CONF_STUDIO_NAME,
    DOMAIN, OPT_WATCHED_OFFER_IDS,
)


def _offer(*, hours_to_start: float = 48, offer_id: int = 1) -> Offer:
    start = datetime.now(timezone.utc) + timedelta(hours=hours_to_start)
    return Offer(
        offer_id=offer_id, class_name="Pilates", category="Pilates",
        coach="Léa", start_at=start, end_at=start + timedelta(hours=1),
        bookable_at=start - timedelta(days=14),
        is_bookable_now=False, is_waitlist_only=True,
    )


def _waitlist(offer: Offer, *, status: str = "convertible") -> WaitlistEntry:
    return WaitlistEntry(
        entry_id=6521868, offer=offer, status=status, position=None,
    )


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "user@example.com", CONF_PASSWORD: "pw",
            "bsport_token": "tok", "bsport_user_id": 9999999,
            CONF_STUDIO_ID: 538, CONF_STUDIO_NAME: "Chimosa",
        },
        options={OPT_WATCHED_OFFER_IDS: []},
        unique_id="9999999",
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_with_waitlist(
    hass: HomeAssistant,
    waitlist: WaitlistEntry,
    *,
    book_mock: AsyncMock | None = None,
) -> MockConfigEntry:
    overview = AccountOverview(
        waitlists=(waitlist,), bookings=(), active_pass=None, membership=None,
    )
    entry = _entry(hass)
    book = book_mock or AsyncMock(
        return_value=Booking(
            booking_id=1, offer=waitlist.offer, status="confirmed",
        )
    )
    with patch(
        "custom_components.bsport.api.client.BsportClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.get_account_overview",
        new=AsyncMock(return_value=overview),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(waitlist,)),
    ), patch(
        "custom_components.bsport.api.client.BsportClient.book_offer",
        new=book,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data._test_book = book  # type: ignore[attr-defined]
    return entry


def _switch_entity_id(hass: HomeAssistant, entry: MockConfigEntry, offer_id: int) -> str:
    ent_reg = er.async_get(hass)
    expected_uid = (
        f"{DOMAIN}_{entry.entry_id}_waitlist_autobook_{offer_id}"
    )
    matches = [e for e in ent_reg.entities.values() if e.unique_id == expected_uid]
    assert matches, (
        f"switch unique_id {expected_uid!r} not registered. "
        f"Registered: {[e.unique_id for e in ent_reg.entities.values()]}"
    )
    return matches[0].entity_id


# ── 1. Entity registration ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_entity_registered_per_waitlist(hass: HomeAssistant):
    offer = _offer(offer_id=42)
    waitlist = _waitlist(offer, status="waiting")
    entry = await _setup_with_waitlist(hass, waitlist)
    entity_id = _switch_entity_id(hass, entry, 42)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF


# ── 2. Default off ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_defaults_off(hass: HomeAssistant):
    offer = _offer(hours_to_start=48)
    waitlist = _waitlist(offer, status="convertible")
    entry = await _setup_with_waitlist(hass, waitlist)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    assert coord._auto_book_enabled is False
    # And no auto-book was triggered during setup
    assert entry.runtime_data._test_book.await_count == 0


# ── 3. Restore on restart ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_restores_on_state(hass: HomeAssistant):
    """When async_get_last_state returns STATE_ON, async_added_to_hass
    flips the coordinator flag back on."""
    offer = _offer(hours_to_start=48, offer_id=7)
    waitlist = _waitlist(offer, status="waiting")  # not convertible — no auto-book on restore
    fake_state = State("switch.fake_autobook", STATE_ON)
    with patch(
        "custom_components.bsport.switch.WaitlistAutoBookSwitch.async_get_last_state",
        new=AsyncMock(return_value=fake_state),
    ):
        entry = await _setup_with_waitlist(hass, waitlist)
        await hass.async_block_till_done()

    coord = entry.runtime_data.waitlists[offer.offer_id]
    assert coord._auto_book_enabled is True
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 4. Turn ON triggers immediate book when convertible + lead-time OK ──────


@pytest.mark.asyncio
async def test_turn_on_triggers_book_when_conditions_met(
    hass: HomeAssistant,
):
    offer = _offer(hours_to_start=48)  # well outside default 24h lead time
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock(
        return_value=Booking(booking_id=1, offer=offer, status="confirmed")
    )
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    succeeded: list = []
    hass.bus.async_listen(
        "bsport_book_succeeded", lambda e: succeeded.append(e),
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 1
    assert len(succeeded) == 1
    assert succeeded[0].data["source"] == "autobook"


# ── 5. Turn ON, lead-time NOT satisfied → no book ────────────────────────────


@pytest.mark.asyncio
async def test_turn_on_skips_when_inside_lead_time(hass: HomeAssistant):
    offer = _offer(hours_to_start=2)  # inside default 24h lead time
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock()
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 0
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 6. Status-flip on poll triggers book when switch is ON ───────────────────


@pytest.mark.asyncio
async def test_status_transition_triggers_auto_book_when_on(
    hass: HomeAssistant,
):
    offer = _offer(hours_to_start=48, offer_id=11)
    waiting = _waitlist(offer, status="waiting")
    convertible = _waitlist(offer, status="convertible")
    book = AsyncMock(
        return_value=Booking(booking_id=1, offer=offer, status="confirmed")
    )
    entry = await _setup_with_waitlist(hass, waiting, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    # Turn on while still waiting — no book yet.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()
    assert book.await_count == 0

    coord = entry.runtime_data.waitlists[offer.offer_id]
    # Force the next batch fetch to return the convertible state.
    with patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(convertible,)),
    ):
        coord._batch.invalidate()
        await coord.async_refresh()
        await hass.async_block_till_done()

    assert book.await_count == 1


# ── 7. Status-flip with switch OFF → no book ─────────────────────────────────


@pytest.mark.asyncio
async def test_status_transition_does_not_book_when_off(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=12)
    waiting = _waitlist(offer, status="waiting")
    convertible = _waitlist(offer, status="convertible")
    book = AsyncMock()
    entry = await _setup_with_waitlist(hass, waiting, book_mock=book)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    with patch(
        "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
        new=AsyncMock(return_value=(convertible,)),
    ):
        coord._batch.invalidate()
        await coord.async_refresh()
        await hass.async_block_till_done()

    assert book.await_count == 0


# ── 8. Failure leaves switch on, fires failed event ──────────────────────────


@pytest.mark.asyncio
async def test_failure_leaves_switch_on(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=13)
    waitlist = _waitlist(offer, status="convertible")
    book = AsyncMock(
        side_effect=BsportBookError(
            reason="payment_required", status=402, raw_body="",
        )
    )
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    failed: list = []
    hass.bus.async_listen(
        "bsport_book_failed", lambda e: failed.append(e),
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
    )
    await hass.async_block_till_done()

    assert book.await_count == 1
    assert len(failed) == 1
    assert failed[0].data["source"] == "autobook"
    assert failed[0].data["reason"] == "payment_required"
    state = hass.states.get(entity_id)
    assert state is not None and state.state == STATE_ON


# ── 9. Retry on next poll while still convertible ───────────────────────────


@pytest.mark.asyncio
async def test_retry_on_next_poll_after_failure(hass: HomeAssistant):
    offer = _offer(hours_to_start=48, offer_id=14)
    waitlist = _waitlist(offer, status="convertible")
    booking = Booking(booking_id=1, offer=offer, status="confirmed")
    book = AsyncMock(
        side_effect=[
            BsportBookError(reason="cannot_book", status=423, raw_body=""),
            BsportBookError(reason="cannot_book", status=423, raw_body=""),
            booking,
        ]
    )
    # async_book has its own discard+retry path on cannot_book + convertible;
    # make discard succeed so we don't accidentally test that path here.
    discard = AsyncMock(return_value=None)
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    entity_id = _switch_entity_id(hass, entry, offer.offer_id)

    with patch(
        "custom_components.bsport.api.client.BsportClient.discard_waitlist",
        new=discard,
    ):
        # First turn on triggers an attempt that fails (cannot_book +
        # convertible → discard → retry → cannot_book again → raise).
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
        )
        await hass.async_block_till_done()
        # Second poll attempt — third book_offer call succeeds.
        coord = entry.runtime_data.waitlists[offer.offer_id]
        with patch(
            "custom_components.bsport.api.client.BsportClient.list_waitlists_with_positions",
            new=AsyncMock(return_value=(waitlist,)),
        ):
            coord._batch.invalidate()
            await coord.async_refresh()
            await hass.async_block_till_done()

    assert book.await_count >= 2  # at least one retry happened


# ── 10. Lock prevents concurrent auto+manual book ────────────────────────────


@pytest.mark.asyncio
async def test_lock_serialises_concurrent_books(hass: HomeAssistant):
    """When the lock is held, async_maybe_auto_book no-ops; only one book
    proceeds even under contention."""
    import asyncio
    offer = _offer(hours_to_start=48, offer_id=15)
    waitlist = _waitlist(offer, status="convertible")
    booking = Booking(booking_id=1, offer=offer, status="confirmed")
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_book(_offer_id):
        started.set()
        await proceed.wait()
        return booking

    book = AsyncMock(side_effect=slow_book)
    entry = await _setup_with_waitlist(hass, waitlist, book_mock=book)
    coord = entry.runtime_data.waitlists[offer.offer_id]
    coord._auto_book_enabled = True

    # Kick off a manual book; it'll grab the lock and stall.
    manual = asyncio.create_task(coord.async_book(source="service"))
    await started.wait()
    # Try auto-book while the lock is held — it should no-op.
    await coord.async_maybe_auto_book()
    proceed.set()
    await manual
    await hass.async_block_till_done()

    assert book.await_count == 1  # only the manual call ran
```

- [ ] **Step 2: Run the new test file**

Run: `.venv/bin/python -m pytest tests/test_switch.py -xvs`

Expected: all tests PASS. If any fail, fix incrementally — most likely culprits are entity_id resolution timing or restore-cache wiring.

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -x`

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_switch.py
git commit -m "test(switch): cover toggle, restore, retry, lock-serialisation"
```

---

## Task 8: Options-flow menu item for setting lead time

**Files:**
- Modify: `custom_components/bsport/config_flow.py:192-201` (menu) and append a new step.

- [ ] **Step 1: Write a failing test for the lead-time option roundtrip**

Append to `tests/test_options_flow.py`:

```python
@pytest.mark.asyncio
async def test_options_set_auto_book_lead_time_roundtrip(hass: HomeAssistant):
    """Setting the lead-time via the options flow stores seconds in
    entry.options under OPT_AUTO_BOOK_LEAD_TIME."""
    from custom_components.bsport.const import OPT_AUTO_BOOK_LEAD_TIME

    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "set_auto_book_lead_time"},
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"hours": 6},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][OPT_AUTO_BOOK_LEAD_TIME] == 6 * 3600


@pytest.mark.asyncio
async def test_options_set_auto_book_lead_time_rejects_negative(
    hass: HomeAssistant,
):
    entry = _entry_with_runtime(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "set_auto_book_lead_time"},
    )
    # voluptuous range raises during configure → MultipleInvalid surfaces
    # as a re-shown form with the schema validation error. Assert the
    # config_entry option is unchanged.
    from voluptuous.error import MultipleInvalid
    with pytest.raises(MultipleInvalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"hours": -1},
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_options_flow.py -xvs -k auto_book_lead_time`

Expected: FAIL — `set_auto_book_lead_time` step not registered.

- [ ] **Step 3: Add the menu option and step**

Edit `custom_components/bsport/config_flow.py`. Update imports first (lines 18-29):

```python
from .const import (
    CONF_BSPORT_TOKEN,
    CONF_BSPORT_USER_ID,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_STUDIO_COVER,
    CONF_STUDIO_ID,
    CONF_STUDIO_NAME,
    DEFAULT_AUTO_BOOK_LEAD_TIME,
    DOMAIN,
    KNOWN_STUDIOS,
    MAX_AUTO_BOOK_LEAD_TIME_HOURS,
    OPT_AUTO_BOOK_LEAD_TIME,
    OPT_WATCHED_OFFER_IDS,
)
```

Update the `async_step_init` method (lines 195-201) to include the new menu item:

```python
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_watch", "remove_watch", "set_auto_book_lead_time",
            ],
        )
```

Append a new step method to `BsportOptionsFlow`, after `async_step_remove_watch`:

```python
    async def async_step_set_auto_book_lead_time(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Set the global auto-book minimum lead time, in hours."""
        current_seconds = self.config_entry.options.get(
            OPT_AUTO_BOOK_LEAD_TIME,
            int(DEFAULT_AUTO_BOOK_LEAD_TIME.total_seconds()),
        )
        current_hours = int(current_seconds // 3600)
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required("hours", default=current_hours): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=MAX_AUTO_BOOK_LEAD_TIME_HOURS),
                    ),
                }
            )
            return self.async_show_form(
                step_id="set_auto_book_lead_time",
                data_schema=schema,
            )

        new_seconds = int(user_input["hours"]) * 3600
        return self.async_create_entry(
            title="",
            data={
                **self.config_entry.options,
                OPT_AUTO_BOOK_LEAD_TIME: new_seconds,
            },
        )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_options_flow.py -xvs -k auto_book_lead_time`

Expected: both PASS.

- [ ] **Step 5: Run the full options-flow test file**

Run: `.venv/bin/python -m pytest tests/test_options_flow.py -xvs`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bsport/config_flow.py tests/test_options_flow.py
git commit -m "feat(options): add Auto-book lead time option"
```

---

## Task 9: Translations

**Files:**
- Modify: `custom_components/bsport/translations/en.json`, `custom_components/bsport/translations/fr.json`.

- [ ] **Step 1: Update `en.json`**

Edit `custom_components/bsport/translations/en.json`. Replace the `options.step.init` block (the menu options) and append the new step + a `switch` entity block. The full result should be:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Pick your studio",
        "description": "Select the bsport-powered studio you want to connect. If yours isn't on the list, pick 'Other (enter company id)' and enter the numeric id on the next screen.",
        "data": {
          "studio_id": "Studio"
        }
      },
      "custom_studio": {
        "title": "Custom studio",
        "description": "Enter your bsport company id — the number after 'com.bsport_' in the mobile app's package name (e.g. 538 for Chimosa, 2387 for Mindful Life Berlin).",
        "data": {
          "studio_id": "Company id"
        }
      },
      "credentials": {
        "title": "Sign in",
        "description": "Enter the email and password you use for the studio you just picked.",
        "data": {
          "email": "Email",
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid email or password.",
      "invalid_studio_id": "That doesn't look like a valid studio id.",
      "not_a_member": "Your bsport account isn't a member of the studio you picked. Go back and select a different studio, or sign in with the credentials you use for this one."
    },
    "abort": {
      "cannot_connect": "Cannot reach bsport — check your connection and try again.",
      "unknown": "Unexpected error during setup. Check Home Assistant logs.",
      "already_configured": "This bsport studio is already set up for this user."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "bsport options",
        "menu_options": {
          "add_watch": "Add a watched class",
          "remove_watch": "Remove a watched class",
          "set_auto_book_lead_time": "Set auto-book lead time"
        }
      },
      "add_watch": {
        "title": "Add a watched class",
        "description": "Pick a class that isn't yet open for registration — Home Assistant will notify you (and let you one-tap book) when it becomes bookable.",
        "data": {
          "offer_id": "Class"
        }
      },
      "remove_watch": {
        "title": "Remove watched classes",
        "data": {
          "remove": "Watches to remove"
        }
      },
      "set_auto_book_lead_time": {
        "title": "Auto-book lead time",
        "description": "When auto-book is on for a waitlisted class, the integration will book a freed-up spot only if it opens at least this many hours before class start. Spots opening closer to class time still notify you but won't be booked automatically. Default 24 hours.",
        "data": {
          "hours": "Hours before class"
        }
      }
    },
    "abort": {
      "cannot_connect": "Cannot reach bsport.",
      "no_watches": "No watched classes to remove."
    }
  },
  "entity": {
    "sensor": {
      "next_booking":      { "name": "Next booking" },
      "upcoming_count":    { "name": "Upcoming bookings" },
      "pass_classes_remaining": { "name": "Pass classes remaining" },
      "pass_expires":      { "name": "Pass expires" },
      "membership_status": { "name": "Membership status" },
      "membership_renewal": { "name": "Membership renewal" },
      "waitlist_status":   { "name": "Waitlist status" },
      "waitlist_position": { "name": "Waitlist position" },
      "watch_status":      { "name": "Watch status" },
      "watch_opens_at":    { "name": "Registration opens" }
    },
    "button": {
      "waitlist_book":    { "name": "Book" },
      "waitlist_discard": { "name": "Leave waitlist" },
      "watch_book":       { "name": "Book" }
    },
    "switch": {
      "waitlist_autobook": { "name": "Auto-book" }
    },
    "calendar": {
      "bookings": { "name": "Bookings" }
    }
  },
  "services": {
    "book_offer":       { "name": "Book offer",       "description": "Book a bsport offer." },
    "cancel_booking":   { "name": "Cancel booking",   "description": "Cancel a confirmed booking." },
    "watch_class":      { "name": "Watch class",      "description": "Add a class to the watch list." },
    "unwatch_class":    { "name": "Unwatch class",    "description": "Remove a class from the watch list." },
    "discard_waitlist": { "name": "Leave waitlist",   "description": "Remove yourself from the waiting list for a class." }
  }
}
```

- [ ] **Step 2: Update `fr.json`**

Edit `custom_components/bsport/translations/fr.json`. Make the same structural additions:

In `options.step.init.menu_options`, add:
```json
"set_auto_book_lead_time": "Définir le délai d'auto-réservation"
```

Append to `options.step` (sibling of `remove_watch`):
```json
"set_auto_book_lead_time": {
  "title": "Délai d'auto-réservation",
  "description": "Quand l'auto-réservation est activée pour un cours en liste d'attente, l'intégration ne réserve une place libérée que si elle s'ouvre au moins ce nombre d'heures avant le cours. Les places s'ouvrant plus près du cours vous notifient quand même mais ne sont pas réservées automatiquement. Par défaut 24 heures.",
  "data": {
    "hours": "Heures avant le cours"
  }
}
```

Add a `switch` entity block as a sibling of `button` and `calendar`:
```json
"switch": {
  "waitlist_autobook": { "name": "Auto-réservation" }
}
```

- [ ] **Step 3: Verify JSON validity**

Run: `.venv/bin/python -c "import json; json.load(open('custom_components/bsport/translations/en.json')); json.load(open('custom_components/bsport/translations/fr.json')); print('ok')"`

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add custom_components/bsport/translations/en.json custom_components/bsport/translations/fr.json
git commit -m "i18n: strings for auto-book switch and lead-time option"
```

---

## Task 10: Bump version, full smoke run, commit

**Files:**
- Modify: `custom_components/bsport/manifest.json:11`.

- [ ] **Step 1: Bump version**

Edit `custom_components/bsport/manifest.json`. Change line 11 from `"version": "1.0.14"` to `"version": "1.0.15"`.

- [ ] **Step 2: Run the entire test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: all PASS. If failures appear, address them before committing.

- [ ] **Step 3: Commit**

```bash
git add custom_components/bsport/manifest.json
git commit -m "chore(manifest): bump version to 1.0.15"
```

---

## Self-review checklist (for the implementer to run after Task 10)

- [ ] All spec sections (§2 UX, §3 architecture, §4 data flow, §5 error handling, §6 testing) map to one or more tasks above.
- [ ] No `TBD` / `TODO` / "implement later" markers remain.
- [ ] `_auto_book_enabled`, `_auto_book_lead_time`, `_book_lock`, and `async_maybe_auto_book` names are consistent across coordinator code, switch code, and tests.
- [ ] `BOOK_SOURCE_AUTOBOOK = "autobook"` matches the string literal used in the `Literal` type and tests.
- [ ] Switch unique_id `bsport_<entry_id>_waitlist_autobook_<offer_id>` matches between switch.py and test helpers.
- [ ] Translation keys (`waitlist_autobook`, `set_auto_book_lead_time`) match between code and JSON.
