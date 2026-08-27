#!/usr/bin/env python3
"""Offline validation of connection.py using a fake spa + fake pybalboa.

Simulates the three failure modes observed in the 24h monitor data:
  1. Spa OFFLINE (connect refused/timeout)     -> expect exponential backoff
  2. ZOMBIE socket (TCP ok, no data)           -> expect zombie_socket + backoff
  3. Mid-session DROP (connected, then dies)   -> expect conn_lost + reconnect
Plus pause/resume semantics.

Run:  python3 validate_local.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ---- fake pybalboa so connection.py imports cleanly without the real lib ----
fake = types.ModuleType("pybalboa")


class FakeSpaClient:
    """Behaviour controlled by module-level MODE."""

    MODE = "ok"  # ok | refuse | zombie | drop_after_2s

    def __init__(self, host, port=4257):
        self.host, self.port = host, port
        self.connected = False
        self.last_message_received = None
        self._drop_task = None

    async def connect(self):
        if FakeSpaClient.MODE == "refuse":
            raise ConnectionRefusedError("simulated offline")
        self.connected = True
        if FakeSpaClient.MODE == "drop_after_2s":
            self._drop_task = asyncio.get_event_loop().call_later(
                2.0, self._drop
            )
        return True

    def _drop(self):
        self.connected = False

    async def async_configuration_loaded(self):
        if FakeSpaClient.MODE == "zombie":
            await asyncio.sleep(999)  # never sends data
        self.last_message_received = datetime.now(timezone.utc)
        return True

    async def disconnect(self):
        self.connected = False


fake.SpaClient = FakeSpaClient
sys.modules["pybalboa"] = fake

from connection import ManagerConfig, ManagerState, SpaConnectionManager  # noqa: E402

EVENTS: list[dict] = []
CFG = ManagerConfig(
    connect_timeout=1.0,
    backoff_initial=0.3,
    backoff_max=2.0,
    heartbeat_interval=0.5,
    stale_after=5.0,
)


def expect(cond: bool, msg: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        raise SystemExit(f"VALIDATION FAILED: {msg}")


def events(name: str) -> list[dict]:
    return [e for e in EVENTS if e["event"] == name]


async def scenario(title: str, mode: str, run_s: float) -> SpaConnectionManager:
    print(f"\n=== {title} (mode={mode}) ===")
    EVENTS.clear()
    FakeSpaClient.MODE = mode
    mgr = SpaConnectionManager("fake-spa", config=CFG, event_cb=EVENTS.append)
    await mgr.start()
    await asyncio.sleep(run_s)
    return mgr


async def main() -> None:
    # 1. OFFLINE -> exponential backoff
    mgr = await scenario("Offline spa", "refuse", 3.0)
    backs = [float(e["backoff_s"]) for e in events("backoff")]
    expect(len(events("connect_fail")) >= 3, f"multiple failed attempts ({len(events('connect_fail'))})")
    expect(len(backs) >= 2 and backs[1] > backs[0], f"backoff grows: {backs[:4]}")
    expect(max(backs) <= CFG.backoff_max, "backoff capped at max")
    await mgr.stop()

    # 2. ZOMBIE socket -> detected, treated as failure
    mgr = await scenario("Zombie socket", "zombie", 4.0)
    expect(len(events("zombie_socket")) >= 1, "zombie socket detected")
    expect(len(events("connect_ok")) == 0, "zombie never counted as connected")
    await mgr.stop()

    # 3. HEALTHY connect -> connected + heartbeats
    mgr = await scenario("Healthy spa", "ok", 2.0)
    expect(len(events("connect_ok")) == 1, "connected once")
    expect(mgr.state is ManagerState.CONNECTED, "state == CONNECTED")
    expect(len(events("heartbeat_ok")) >= 1, "heartbeats running")

    # 4. PAUSE / RESUME
    await mgr.pause()
    expect(mgr.state is ManagerState.PAUSED, "paused state")
    n_attempts = len(events("connect_attempt"))
    await asyncio.sleep(1.5)
    expect(len(events("connect_attempt")) == n_attempts, "no retries while paused")
    await mgr.resume()
    await asyncio.sleep(1.0)
    expect(mgr.state is ManagerState.CONNECTED, "reconnected after resume")
    await mgr.stop()

    # 5. MID-SESSION DROP -> conn_lost -> auto reconnect
    mgr = await scenario("Mid-session drop", "drop_after_2s", 4.5)
    expect(len(events("connection_lost")) >= 1, "connection loss detected by heartbeat")
    expect(len(events("connect_ok")) >= 2, "auto-reconnected after drop")
    await mgr.stop()

    print("\nALL SCENARIOS PASSED — connection.py state machine is sound.\n")


asyncio.run(main())


# ---- extra: reachable gating semantics (run standalone) ----
async def _reachable_test():
    import connection as C
    print("\n=== Reachable gating (stable_for) ===")
    EVENTS.clear()
    FakeSpaClient.MODE = "ok"
    cfg = ManagerConfig(connect_timeout=1.0, backoff_initial=0.3,
                        heartbeat_interval=0.5, stale_after=5.0, stable_for=1.0)
    mgr = SpaConnectionManager("fake-spa", config=cfg, event_cb=EVENTS.append)
    await mgr.start()
    await asyncio.sleep(0.3)  # connected, but < stable_for
    expect(mgr.connected, "connected quickly")
    expect(not mgr.reachable, "NOT reachable before stable_for elapses")
    await asyncio.sleep(1.0)  # now past stable_for
    expect(mgr.reachable, "reachable after stable_for")
    await mgr.pause()
    expect(not mgr.reachable, "not reachable while paused")
    await mgr.stop()
    print("  reachable gating OK")

asyncio.run(_reachable_test())
