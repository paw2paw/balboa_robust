# Changelog

All notable changes to Balboa Spa (Robust) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions
follow [SemVer](https://semver.org/).

Context: this integration exists to work around the Balboa Wi-Fi module
**part number 50350** (paired with `BW6013X1` control systems), which
exhibits stale-socket disconnects, 30-second silent windows, and
overnight dead zones of 100 min+. Measured uptime on a real unit:
**33.85 %**. See [README](README.md#why-this-exists) for the full story.

## [0.3.2] – 2026-08-29

Dashboard-and-docs polish release. No code changes, no entity changes.

### Changed
- Example dashboard `dashboards/spa.yaml`:
  - Fault card renamed *Last Spa Error* → **Last Spa Fault Report**.
  - Date format now `%a %b %d, %H:%M` (weekday + comma) rendered in
    local timezone via `timestamp_custom(..., true)`.
  - Fault card structure switched from `>` folded to `|` literal so YAML
    never eats a space inside the format string. When/Fault are on
    separate lines for readability.
  - Reachable tile in the Spa hero uses `color: state` and no icon
    override — HA auto-picks green/red and swaps
    `lan-connect`/`lan-disconnect` per state.
- `README.md`: header logo now uses an absolute `raw.githubusercontent.com`
  URL so it renders on the HACS repository page (relative paths only work
  when viewed via github.com).

### Not shipped
- Attempted a centered uptime title via inline `<h3 align="center">`;
  HA's markdown sanitizer strips `align=` and `style=`, so it doesn't
  render. Left as plain left-aligned markdown. Real centering needs
  `type: heading` (no template support) or `card-mod` (HACS dep we're
  avoiding).

## [0.3.1] – 2026-08-29

Small dashboard-quality release, all discovered while pointing the
example dashboard at a real 0.3.0 install.

### Added
- `sensor.<slug>_uptime_rolling` now exposes two attributes:
  `window_seconds` (int) and `window_human` (e.g. `"1 h"`, `"24 h"`).
  Dashboards can label the gauge dynamically from the configured
  rolling window instead of hard-coding "1 h" — see the updated example
  in `dashboards/spa.yaml`.

### Fixed
- Example dashboard `dashboards/spa.yaml` had six broken entity IDs. HA
  auto-generates entity_ids from the *slugified friendly name*, not the
  code's `key`. The following are the actual entity_id suffixes and are
  now used in the example:
  - `uptime_ratio` → `sensor.<slug>_uptime_rolling`
  - `current_backoff` → `sensor.<slug>_current_backoff_delay`
  - `next_attempt_at` → `sensor.<slug>_next_connection_attempt`
  - `connects_ok` → `sensor.<slug>_successful_connects`
  - `connects_failed` → `sensor.<slug>_failed_connects`
  - `voltage` → `sensor.<slug>_line_voltage`
- Example dashboard reorganised for 3 views (Spa / Setup / Health), a
  conditional "Last Spa Error" markdown card at the end of the Spa
  view (invisible until a fault fires), and a colour-coded uptime
  gauge with dynamic-window label.

### Migration notes
- No breaking changes. Existing installs upgrade cleanly. The new
  attribute is additive.
- If you copied the previous `dashboards/spa.yaml` verbatim before this
  release, either re-copy or apply the six substitutions above by hand.

## [0.3.0] – 2026-08-29

Entity expansion + long-standing Heat Mode / Temperature Range bug fix.
See [PLAN.md](PLAN.md) for the full design doc; highlights:

### Fixed
- **Heat mode and Temperature range selects now register.** The
  `SpaControlSelect` constructor rejected the `category` kwarg passed
  by the discovery code, so both entities silently failed to appear.
  Existing installs get them automatically on first startup after
  upgrade — no reconfig needed.

### Added
- `switch.<slug>_aux_N` and `switch.<slug>_mister_N` — one per aux /
  mister control the spa module exposes. Absent on modules without them.
- `sensor.<slug>_spa_state` — current spa mode (running / initializing /
  hold / test / ab-temps).
- `sensor.<slug>_wifi_state` (Diagnostic) — the Balboa module's own view
  of its Wi-Fi link (ok / prime / hold / panel / startup / not-comm).
- `sensor.<slug>_spa_time` and `sensor.<slug>_clock_offset` (Diagnostic)
  — the spa's internal clock and how far it has drifted from HA.
- `sensor.<slug>_filter_{1,2}_duration` — filter-cycle length in
  minutes, registered when the module has one configured.
- **New service `balboa_robust.sync_spa_clock`** — pushes HA's current
  wall time to the spa. Useful after a power cut or drift.
- Config flow now asks for a **Name** at setup (default `Spa`), used as
  the device name and entity-friendly-name prefix. Existing installs
  can rename via the Options flow's new **Rename** step. Entity IDs and
  unique IDs are unchanged.

### Changed
- `switch.<slug>_pause_connection` moved from **Configuration** to
  **Diagnostic** — it's about integration health, not spa setup.
  Existing installs are re-categorised on first startup after upgrade.
- Entity display names shortened for readability (entity IDs unchanged
  — automations unaffected):
  - `Filter cycle N …` → `Filter N …` across the board
  - `Circulation pump running` → `Circulation running`

### Test harness
- First tests land: `tests/` with `pytest-homeassistant-custom-component`,
  covering capability-gates for aux / mister / wifi_state discovery.
  New CI workflow `.github/workflows/tests.yml`.

## [0.2.0] – 2026-08-27

Big refactor of the entity surface to mirror the design language of the
stock Home Assistant `balboa` integration, plus fixes for two real-world
bugs surfaced during the first deploy.

### Breaking changes — read before upgrading

Ten sensor entities from 0.1.0 have been replaced by richer entity types.
The integration **auto-deletes the obsolete registry entries** on first
startup after the upgrade, so the HA UI cleans itself up. However, if you
referenced any of the old entity IDs from **automations, scripts, or
dashboard cards**, those references will break silently — HA will show
them as *unavailable* with a broken-link icon.

| Removed in 0.2.0 (auto-deleted from registry) | Replaced by |
|---|---|
| `sensor.<slug>_filter_cycle_1_start` | `time.<slug>_filter_cycle_1_start`  *(editable)* |
| `sensor.<slug>_filter_cycle_1_end`   | `time.<slug>_filter_cycle_1_end`    *(editable)* |
| `sensor.<slug>_filter_cycle_1_duration_min` | *removed — derive from start/end if needed* |
| `sensor.<slug>_filter_cycle_2_start` | `time.<slug>_filter_cycle_2_start`  *(editable)* |
| `sensor.<slug>_filter_cycle_2_end`   | `time.<slug>_filter_cycle_2_end`    *(editable)* |
| `sensor.<slug>_filter_cycle_2_duration_min` | *removed — derive from start/end if needed* |
| `sensor.<slug>_last_fault`     | `event.<slug>_fault`  *(HA event log, richer)* |
| `sensor.<slug>_last_fault_at`  | `event.<slug>_fault` — `fault_date` attribute |
| `sensor.<slug>_heat_mode`      | `select.<slug>_heat_mode`  *(was a duplicate)* |
| `sensor.<slug>_temperature_range` | `select.<slug>_temperature_range`  *(duplicate)* |

`<slug>` is your device slug — usually the device name lowercased, e.g.
`garden_spa`.

**How to find your broken references (5-minute audit):**

1. **Automations & scripts** — Developer Tools → YAML → *Check
   configuration*. Any automation referencing a removed entity ID surfaces
   as a validation error. Also Settings → Automations & Scenes, then click
   through anything with a warning triangle.
2. **Dashboards / Lovelace** — open each dashboard in Edit mode; broken
   cards show a red *"Entity not available"* banner. Fastest scan:
   Developer Tools → Statistics → filter by domain `sensor`, look for
   entries flagged `orphaned`. Those are the ones you can safely remove.
3. **Global search** — Settings → System → three-dot menu →
   *Repairs*. HA lists all entity references that no longer resolve to a
   live entity.

If you had none of those references, upgrading is fully automatic — no
action required beyond restarting HA.

### Added

* **`event.<slug>_fault`** — HA-native event entity for the spa fault log.
  Triggers per fault code (`flow_failed`, `heater_dry`, etc.); attributes
  carry the fault datetime and raw code. Rendered as a live log by the
  standard HA logbook card.
* **`time.<slug>_filter_cycle_{1,2}_{start,end}`** — filter-cycle schedule
  is now *editable* from the device page (Configuration section). Writes
  go through `pybalboa.configure_filter_cycle`.
* **`switch.<slug>_filter_cycle_2_enabled`** — turn cycle 2 on/off (cycle
  1 is always on per module firmware).
* **`select.<slug>_heat_mode`** — READY / REST toggle, native HA select.
* **`select.<slug>_temperature_range`** — LOW / HIGH temperature range.
* **`switch.<slug>_all_pumps`** — master switch that drives every pump to
  its highest setting (or off).
* **`fan.<slug>_pump_N`** — proper Fan entities with preset modes
  (`off` / `low` / `high` for two-speed pumps, `off` / `on` for
  single-speed). Replaces the switch-style representation.
* **`sensor.<slug>_heat_state`** — current heater state (`off` /
  `heating` / `heat_waiting`).
* **`sensor.<slug>_voltage`** — spa supply voltage (diagnostic).
* **`binary_sensor.<slug>_filter_cycle_{1,2}_running`** and
  **`binary_sensor.<slug>_circulation_pump_running`** — live status.
* **Sample dashboard** at [`dashboards/spa.yaml`](dashboards/spa.yaml) —
  tiled Sections view with Pumps, Filter cycles, Status, Connection
  health, and Fault log sections. See "Dashboard" below for install
  instructions.
* Device info now includes the spa's MAC as a Home Assistant *connection*
  (so it links up in the device graph).

### Changed

* Config-flow **initial probe now retries for ~90 s** instead of failing
  after one 10-second attempt. The known-bad WiFi module is silent
  roughly 2/3 of the time, so a single probe frequently landed in a dead
  window; adding the retry loop lets initial setup ride through a
  typical stale-recovery cycle.
* Options schema: fixed `NumberSelectorConfig` to omit
  `unit_of_measurement` when unset — HA 2026.8+ rejects `None` there and
  the Configure button returned `400 Bad Request` on affected setups.
* `EntityCategory` reshuffle so entities land in the intended box on the
  device page: pumps/lights/all-pumps/temp-range → Controls,
  filter-cycle schedule + heat mode + pause → Configuration, live spa
  state → Sensors, connection health → Diagnostic, fault → Events.

### Removed

* All ten sensor entities in the breaking-changes table above.
* Duplicate `heat_mode` / `temperature_range` *sensors* — they were
  read-only twins of the new selects.

### Migration hook

`custom_components/balboa_robust/__init__.py` gained
`_remove_obsolete_entities()`, which runs on every setup and deletes any
registry entry whose `unique_id` matches the ten obsolete keys above.
Idempotent — safe on repeat installs. Does not touch entities from other
integrations.

---

## [0.1.0] – 2026-08-26

Initial release.

* Supervised connection manager (`connection.py`, pure Python, no HA
  imports) with configurable timeout, exponential backoff, heartbeat,
  zombie-socket detection, pause/resume, and rolling uptime tracking.
* Config flow with host/port and hot-applied options for every knob.
* Entities: `climate.spa`, `binary_sensor.reachable`, `switch.pause`, and
  7 diagnostic sensors (connection state, connect latency, uptime,
  uptime %, counters).
* 24-hour Mac soak harness (`soak/`) driving the *same* `connection.py`
  the shipped integration uses.
