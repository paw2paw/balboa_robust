# Balboa Spa (Robust)

Home Assistant custom integration for Balboa spas whose Wi-Fi module
misbehaves — stale sockets, silent drop-outs, module going dark for
30-second bursts (or 100-minute overnight zones). Wraps
[`pybalboa`](https://github.com/garbled1/pybalboa) with a supervised
connection manager and exposes clean, HA-native entities on top.

**Not a replacement for the stock `balboa` integration** — if your module
is well-behaved, keep using stock. This one only earns its keep when
you're getting `stale_30s` disconnects, hangs during setup, or a
`sensor.spa_*` that reports `Unavailable` several times a day.

## Why this exists

Built around one specific piece of bad hardware: the **Balboa Wi-Fi module
part number 50350** ("BWA Wi-Fi"), commonly paired with `BW6013X1`-series
spa control systems. Observable behaviour (measured over a 14.75-hour soak
against a real unit at signal strength −44 dBm, i.e. *excellent* Wi-Fi):

* **Effective uptime: 33.85 %** — the module is unreachable 2/3 of the time.
* **41 of 42 disconnects were "stale sockets"** — the module accepts a
  TCP connection, streams data for ~30 seconds, then *goes silent without
  closing the socket*. To a naïve client the connection looks fine; no
  data ever arrives again.
* **Overnight silent windows** — the peak was **104 minutes** at 05:17.
* **Not a Wi-Fi problem.** RSSI −44 dBm rules out interference; it's the
  module firmware.

The stock `pybalboa`-based integration reads from those stale sockets
until timeout, leaking memory and — in some cases — crashing Home
Assistant. This project fixes it in software (heartbeat-based staleness
detection, zombie-socket rejection, exponential backoff, supervised
reconnect) so the module remains usable while you decide whether to
replace it.

**If you own a 50350 module and Home Assistant reports your spa as
"Unavailable" for hours at a time, this is for you.** Long-term the fix
is a hardware bridge or module replacement; the [issue tracker](https://github.com/paw2paw/balboa_robust/issues)
also tracks upstreaming these patches into `pybalboa` so everyone
benefits.

## What it does differently

* **Short connect timeout** (5 s, configurable) — no hangs
* **Zombie-socket detection** — TCP SYN-ACK without spa data is treated
  as a failure, not a success
* **Exponential backoff** — 5 s → 10 s → 20 s → … → 120 s (all tunable)
* **Heartbeat + staleness check** while connected — the spa streams
  ~1 message/second; silence past `stale_after` = link is dead
* **Auto-reconnect** on mid-session drops, with a rolling **uptime %**
  sensor so you can see how flaky your module really is
* **Pause switch + service** — suspend retries during maintenance
  without disabling entities
* **Everything tunable in the UI** and hot-applied without restart

The `connection.py` module has zero HA imports; the 24-hour soak harness
in `soak/` drives the **same file** the shipped integration uses, so what
you measure on the Mac is what you get in HA.

## Install (HACS)

1. HACS → three-dot menu → *Custom repositories* → add
   `https://github.com/paw2paw/balboa_robust` as an **Integration**.
2. Search HACS for "Balboa Spa (Robust)" and install.
3. Restart Home Assistant.
4. Settings → Devices & Services → **+ Add Integration** → *"Balboa Spa
   (Robust)"* → host `192.168.1.100` (yours) + port `4257`.

Setup does a live probe with a 90-second retry budget — safe to click
Submit while the module is in a dead window.

## Upgrading from 0.1.x

**Read [`CHANGELOG.md`](CHANGELOG.md) before restarting.** 0.2.0 replaces
ten sensor entities with richer types (`event`, `time`, `select`,
`switch`) and auto-cleans the obsolete registry rows on first boot.

If you had automations, scripts, or dashboard cards referencing entities
like `sensor.spa_last_fault` or `sensor.spa_filter_cycle_1_start`, they
will break silently — the CHANGELOG has a mapping table plus a
5-minute audit checklist for finding orphaned references.

## Dashboard

A ready-to-paste Sections-style dashboard lives at
[`dashboards/spa.yaml`](dashboards/spa.yaml). Install:

1. Settings → Dashboards → **+ Add Dashboard** → "New dashboard from
   scratch".
2. Open the new dashboard, click the pencil (Edit), then the
   three-dot menu → **Raw configuration editor**.
3. Paste the contents of `dashboards/spa.yaml`. Save.
4. Search-and-replace `garden_spa` with your device slug if you named
   your spa something else.

Layout:

| Section | What's in it |
|---|---|
| Main | Thermostat + LOW / HIGH temperature range |
| Pumps & light | All-pumps master, each pump with speed picker, light |
| Filter cycles | Cycle 1 & 2 running, editable start/end times, cycle 2 enable |
| Status | Reachable, heat state, heat mode, pause switch |
| Connection health | Connection state, uptime %, latency, counters, 24 h history graph |
| Fault log | Native HA event log for spa faults |

## Entities

Fresh install of 0.2.0 registers:

| Section on device page | Entities |
|---|---|
| **Controls** | `climate.<slug>`, `fan.<slug>_pump_N`, `switch.<slug>_all_pumps`, `light.<slug>_light_N`, `select.<slug>_temperature_range` |
| **Configuration** | `select.<slug>_heat_mode`, `switch.<slug>_pause_connection`, `switch.<slug>_filter_cycle_2_enabled`, `time.<slug>_filter_cycle_{1,2}_{start,end}` |
| **Sensors** | `binary_sensor.<slug>_reachable`, `binary_sensor.<slug>_filter_cycle_{1,2}_running`, `binary_sensor.<slug>_circulation_pump_running`, `sensor.<slug>_heat_state` |
| **Diagnostic** | `sensor.<slug>_voltage`, `sensor.<slug>_connection_state`, `sensor.<slug>_connect_latency`, `sensor.<slug>_successful_connects`, `sensor.<slug>_failed_connects`, `sensor.<slug>_connections_lost`, `sensor.<slug>_current_uptime`, `sensor.<slug>_current_downtime`, `sensor.<slug>_uptime_rolling` |
| **Events** | `event.<slug>_fault` |

## History, charts & long-term statistics

The integration builds no custom charts — every entity is typed so HA
records and graphs it natively:

* **History card** — line/timeline for latency, reachable, connection state
* **Long-term statistics** — hourly min/mean/max kept for years for
  uptime %, latency, uptime/downtime seconds, and the cumulative
  counters. Add a *Statistics graph card*.
* **Logbook** — every reconnect and every Reachable transition is logged.

Useful entities to chart:

| Entity | Shows |
|---|---|
| `sensor.<slug>_uptime_rolling` | % of the last hour the spa was reachable |
| `sensor.<slug>_connection_state` | disconnected / connecting / connected / backoff / paused |
| `sensor.<slug>_connect_latency` | how long the last (re)connect took |
| `sensor.<slug>_connections_lost` | cumulative mid-session drops |
| `binary_sensor.<slug>_reachable` | the automation gate (on/off) |
| `event.<slug>_fault` | live spa fault log |

### Recorder retention (optional)

A flaky module produces many state changes. HA's default history purge
is 10 days; if you want longer history for these diagnostics:

```yaml
recorder:
  purge_keep_days: 30
```

You control retention — the integration never changes your recorder
config. Long-term *statistics* are kept independently for years.

## Automations

Gate spa commands on the reachability sensor so they don't fire into a
flapping link:

```yaml
condition:
  - condition: state
    entity_id: binary_sensor.garden_spa_reachable
    state: "on"
```

"Run when the spa comes back" pattern — trigger on the state change with
a debounce, no custom event needed:

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.garden_spa_reachable
    to: "on"
    for: "00:00:30"
```

Pause during maintenance:

```yaml
service: balboa_robust.pause      # before draining/cleaning the tub
service: balboa_robust.resume     # afterwards
```

Or use the **Pause connection** switch on the device page.

## Local development

Validate the state machine (no spa, ~15 s):

```bash
cd soak
python3 validate_local.py
```

24 h soak against the real spa (Mac, needs venv w/ pybalboa):

```bash
cd soak
python3 -m venv venv && source venv/bin/activate && pip install pybalboa
nohup python3 spa_soak_test.py --host 192.168.1.100 --hours 24 \
    > soak_console.log 2>&1 &
disown
tail -f soak.log
```

Analyze:

```bash
pip install pandas matplotlib
python3 analyze_soak.py soak_events.csv
open soak_*.png soak_summary.txt
```

`custom_components/balboa_robust/connection.py` and
`soak/connection.py` **must be byte-identical**. When you edit one:

```bash
cp custom_components/balboa_robust/connection.py soak/connection.py
```

## Files

```
custom_components/balboa_robust/
├── connection.py     ← resilience engine (shared with soak, no HA imports)
├── coordinator.py    ← event bridge to HA entities
├── config_flow.py    ← setup + options UI (initial probe retries ~90 s)
├── __init__.py       ← lifecycle + services + obsolete-entity cleanup
├── climate.py        ← spa thermostat
├── fan.py            ← pumps + blowers with speed presets
├── light.py          ← spa lights
├── switch.py         ← pause, all-pumps, filter-cycle-2 enable
├── select.py         ← heat mode, temperature range
├── binary_sensor.py  ← reachable, filter/circulation running
├── sensor.py         ← heat state, voltage, connection health
├── event.py          ← fault log as native HA events
├── time.py           ← editable filter-cycle start/end times
├── manifest.json
├── strings.json      == translations/en.json
└── translations/en.json

soak/
├── connection.py     ← BYTE-IDENTICAL copy of the core (keep in sync)
├── spa_soak_test.py  ← 24 h collector: manager + ping/tcp/arp/rssi
├── analyze_soak.py   ← soak_summary.txt + 4 PNG charts
└── validate_local.py ← 13 assertions vs. simulated failure modes

dashboards/
└── spa.yaml          ← paste-in Sections dashboard
```

## Credits

* **[`pybalboa`](https://github.com/garbled1/pybalboa)** by [@garbled1](https://github.com/garbled1) — the underlying protocol library
  this integration depends on and uses via PyPI. MIT licensed.
* **Home Assistant core `balboa` integration** — the design patterns for
  the fault `event` entity, filter-cycle `time` entities, and the
  `filter_cycle_2_enabled` switch are lifted from the stock integration.
  Apache 2.0 licensed; compatible with this project's MIT license.

If your spa module is healthy, use the **stock** integration — it's
maintained by the HA team, ships with HA, and has been battle-tested by
thousands of users. This project exists specifically for the
33%-uptime, stale-socket edge case that the stock integration can't
paper over without a rewrite. The long-term goal (see [issue tracker](https://github.com/paw2paw/balboa_robust/issues))
is to distill the heartbeat / zombie-socket / backoff patches back into
a `pybalboa` PR so they benefit everyone.

## License

[MIT](LICENSE).
