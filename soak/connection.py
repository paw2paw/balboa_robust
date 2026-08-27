"""Robust connection manager for Balboa spa WiFi modules.

Pure asyncio / no Home Assistant dependencies, so the exact same file can be
soak-tested standalone (see soak/spa_soak_test.py) before deploying to HA.

State machine:

    DISCONNECTED -> CONNECTING -> CONNECTED
         ^              |            |
         |         (fail)|      (conn lost / stale)
         |              v            v
         +---------- BACKOFF <-------+
                        |
                  (pause/resume)
                        v
                     PAUSED

Every transition emits a structured event via the event callback, which the
soak test writes to CSV and the HA coordinator uses to push entity updates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

# pybalboa is declared in manifest.json "requirements" so HA installs it before
# this module loads. Importing at top means a missing dependency fails loudly
# during setup rather than looking like a spa that is forever "connecting".
from pybalboa import SpaClient
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], None]


class ManagerState(str, Enum):
    """Connection manager states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class ManagerConfig:
    """User-tunable settings (all surfaced in the HA options UI)."""

    connect_timeout: float = 5.0        # TCP connect timeout (seconds)
    backoff_initial: float = 5.0        # first retry delay
    backoff_max: float = 120.0          # retry delay ceiling
    backoff_factor: float = 2.0         # multiplier per failed attempt
    heartbeat_interval: float = 10.0    # health check cadence while connected
    stale_after: float = 30.0           # no spa message for this long = dead
    max_retries: int = 0                # 0 = retry forever
    reconnect_on_error: bool = True     # False = go straight to PAUSED on loss
    auto_pause_after_failures: int = 0  # 0 = never auto-pause
    stable_for: float = 10.0            # reachable=True only after this many
                                        # seconds continuously connected
    uptime_window: float = 3600.0       # window for the rolling uptime % sensor


@dataclass
class ManagerStats:
    """Cumulative statistics, exposed as HA diagnostic sensors."""

    connects_ok: int = 0
    connects_failed: int = 0
    connections_lost: int = 0
    last_connect_ms: Optional[float] = None
    connected_since: Optional[float] = None      # monotonic
    disconnected_since: Optional[float] = None   # monotonic
    total_uptime_s: float = 0.0
    total_downtime_s: float = 0.0

    @property
    def current_uptime_s(self) -> float:
        if self.connected_since is None:
            return 0.0
        return time.monotonic() - self.connected_since

    @property
    def current_downtime_s(self) -> float:
        if self.disconnected_since is None:
            return 0.0
        return time.monotonic() - self.disconnected_since

    # Transition log for windowed uptime %: list of (monotonic_ts, is_up).
    # Trimmed to the lookback window on each query so it can't grow unbounded.
    transitions: list = field(default_factory=list)

    def record_transition(self, is_up: bool) -> None:
        self.transitions.append((time.monotonic(), is_up))

    def uptime_ratio(self, window_s: float) -> Optional[float]:
        """Fraction (0..1) of the last window_s spent connected.

        Returns None until at least one transition exists. Integrates the
        step function of up/down state across the window, clamped to it.
        """
        now = time.monotonic()
        start = now - window_s
        events = [(t, u) for (t, u) in self.transitions if t >= start]
        # Establish state at window start from the last transition before it.
        prior = [(t, u) for (t, u) in self.transitions if t < start]
        if not prior and not events:
            return None
        state_up = prior[-1][1] if prior else events[0][1]
        # Trim stored history to window (keep one just before start as anchor).
        if prior:
            self.transitions = [prior[-1]] + events
        cursor = start
        up_time = 0.0
        for t, u in events:
            if state_up:
                up_time += t - cursor
            cursor = t
            state_up = u
        if state_up:
            up_time += now - cursor
        return max(0.0, min(1.0, up_time / window_s))


class SpaConnectionManager:
    """Owns a pybalboa SpaClient and keeps it alive through flaky WiFi."""

    def __init__(
        self,
        host: str,
        port: int = 4257,
        config: ManagerConfig | None = None,
        event_cb: EventCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.config = config or ManagerConfig()
        self._event_cb = event_cb

        self.client: Any = None  # pybalboa.SpaClient when connected
        self.state: ManagerState = ManagerState.DISCONNECTED
        self.stats = ManagerStats()

        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._resume = asyncio.Event()
        self._paused = False
        self._attempt = 0
        self._backoff = self.config.backoff_initial
        self.next_attempt_at: datetime | None = None
        self._listeners: list[EventCallback] = []
        self.stats.disconnected_since = time.monotonic()
        self.stats.record_transition(False)

    # ------------------------------------------------------------------ API

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def connected(self) -> bool:
        return self.state is ManagerState.CONNECTED

    @property
    def current_backoff_s(self) -> float:
        """Seconds the manager will wait before the next connection attempt.

        Grows with each consecutive failure (backoff_initial * factor^N,
        capped at backoff_max). Resets to backoff_initial on a successful
        connect. Meaningful only while state is BACKOFF; otherwise it's
        the delay that *will* apply if the next attempt fails.
        """
        return self._backoff

    @property
    def reachable(self) -> bool:
        """True only when connected AND stable for config.stable_for seconds.

        This is the signal automations should gate on: it filters out the
        stale/backoff/zombie moments so a command is not fired into a link
        that is currently flapping. It cannot guarantee the spa stays up for
        the duration of a command (nothing can) — pair it with the manager's
        own reconnect, which absorbs unlucky-timing drops.
        """
        if self.state is not ManagerState.CONNECTED:
            return False
        since = self.stats.connected_since
        if since is None:
            return False
        return (time.monotonic() - since) >= self.config.stable_for

    def add_listener(self, cb: EventCallback) -> Callable[[], None]:
        """Subscribe to events; returns an unsubscribe function."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    async def start(self) -> None:
        """Start the supervision loop (returns immediately)."""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name=f"balboa_robust:{self.host}"
        )
        self._emit("manager_started")

    async def stop(self) -> None:
        """Stop supervision and disconnect cleanly."""
        self._stop.set()
        self._resume.set()  # unblock a paused loop so it can exit
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self._teardown_client("stopped")
        self._set_state(ManagerState.STOPPED)
        self._emit("manager_stopped")

    async def pause(self) -> None:
        """User-initiated pause (e.g. tub drained for cleaning).

        Disconnects and suspends all retry attempts until resume().
        Entities remain registered; nothing is disabled.
        """
        self._paused = True
        self._resume.clear()
        await self._teardown_client("paused")
        self._set_state(ManagerState.PAUSED)
        self._emit("paused")

    async def resume(self) -> None:
        """Resume after pause; reconnects with fresh backoff."""
        self._paused = False
        self._attempt = 0
        self._backoff = self.config.backoff_initial
        self._resume.set()
        self._emit("resumed")

    def apply_config(self, config: ManagerConfig) -> None:
        """Hot-apply new options from the HA options flow (no restart)."""
        self.config = config
        self._backoff = min(self._backoff, config.backoff_max)
        self._emit("config_applied", detail=str(config))

    # ------------------------------------------------------------ internals

    async def _run(self) -> None:
        """Supervision loop covering every lifecycle case."""
        while not self._stop.is_set():
            # --- PAUSED: park until resume ---------------------------------
            if self._paused:
                self._set_state(ManagerState.PAUSED)
                await self._resume.wait()
                continue

            # --- CONNECT with timeout --------------------------------------
            self._attempt += 1
            self._set_state(ManagerState.CONNECTING)
            self._emit("connect_attempt", attempt=self._attempt)
            t0 = time.monotonic()
            ok = await self._try_connect()
            connect_ms = (time.monotonic() - t0) * 1000.0

            if ok:
                # --- CONNECTED: reset backoff, monitor health --------------
                self.stats.connects_ok += 1
                self.stats.last_connect_ms = connect_ms
                self._mark_up()
                self._attempt = 0
                self._backoff = self.config.backoff_initial
                self._set_state(ManagerState.CONNECTED)
                self._emit("connect_ok", connect_ms=round(connect_ms, 1))

                reason = await self._monitor_until_unhealthy()
                if self._stop.is_set() or self._paused:
                    continue

                # --- CONNECTION LOST ---------------------------------------
                self.stats.connections_lost += 1
                self._mark_down()
                await self._teardown_client(reason)
                self._emit("connection_lost", detail=reason)

                if not self.config.reconnect_on_error:
                    await self.pause()
                    continue
            else:
                # --- CONNECT FAILED ----------------------------------------
                self.stats.connects_failed += 1
                self._emit(
                    "connect_fail",
                    attempt=self._attempt,
                    connect_ms=round(connect_ms, 1),
                )

                if (
                    self.config.max_retries
                    and self._attempt >= self.config.max_retries
                ):
                    self._emit("max_retries_reached", attempt=self._attempt)
                    await self.pause()
                    continue

                if (
                    self.config.auto_pause_after_failures
                    and self._attempt >= self.config.auto_pause_after_failures
                ):
                    self._emit("auto_paused", attempt=self._attempt)
                    await self.pause()
                    continue

            # --- BACKOFF before next attempt -------------------------------
            self._set_state(ManagerState.BACKOFF)
            self.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                seconds=self._backoff
            )
            self._emit("backoff", backoff_s=self._backoff, attempt=self._attempt)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
            except asyncio.TimeoutError:
                pass  # backoff elapsed, loop retries
            self.next_attempt_at = None
            self._backoff = min(
                self._backoff * self.config.backoff_factor,
                self.config.backoff_max,
            )

    async def _try_connect(self) -> bool:
        """One connection attempt with a hard timeout and clean failure."""
        client = SpaClient(self.host, self.port)
        try:
            connected = await asyncio.wait_for(
                client.connect(), timeout=self.config.connect_timeout
            )
            if not connected:
                await self._safe_disconnect(client)
                return False
            # Wait briefly for the module to actually send configuration —
            # a TCP SYN-ACK alone can be a zombie socket on these modules.
            try:
                loaded = await asyncio.wait_for(
                    client.async_configuration_loaded(),
                    timeout=self.config.connect_timeout * 2,
                )
            except asyncio.TimeoutError:
                loaded = False
            if not loaded:
                self._emit("zombie_socket", detail="tcp ok, no spa data")
                await self._safe_disconnect(client)
                return False
            self.client = client
            return True
        except (asyncio.TimeoutError, OSError, ConnectionError) as err:
            self._emit("connect_error", detail=type(err).__name__)
            await self._safe_disconnect(client)
            return False
        except Exception as err:  # noqa: BLE001 - never kill the loop
            self._emit("connect_error", detail=f"{type(err).__name__}: {err}")
            await self._safe_disconnect(client)
            return False

    async def _monitor_until_unhealthy(self) -> str:
        """Heartbeat loop; returns the reason the connection was declared dead."""
        cfg = self.config
        while not self._stop.is_set() and not self._paused:
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=cfg.heartbeat_interval
                )
                return "stopped"
            except asyncio.TimeoutError:
                pass

            client = self.client
            if client is None:
                return "client_vanished"
            if not getattr(client, "connected", False):
                return "socket_closed"

            # Staleness: the spa normally streams status ~1/sec. Silence
            # beyond stale_after means the link is a zombie even if the
            # socket still looks open.
            last = getattr(client, "last_message_received", None)
            if last is not None:
                try:
                    age = (
                        __import__("datetime").datetime.now(last.tzinfo) - last
                    ).total_seconds()
                except Exception:  # noqa: BLE001
                    age = None
                if age is not None and age > cfg.stale_after:
                    return f"stale_{int(age)}s"

            self._emit("heartbeat_ok", uptime_s=round(self.stats.current_uptime_s))
        return "paused" if self._paused else "stopped"

    async def _teardown_client(self, reason: str) -> None:
        client, self.client = self.client, None
        if client is not None:
            await self._safe_disconnect(client)
            self._emit("client_closed", detail=reason)

    @staticmethod
    async def _safe_disconnect(client: Any) -> None:
        """Disconnect + explicitly close the transport (no zombie sockets)."""
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
        except Exception:  # noqa: BLE001
            pass
        writer = getattr(client, "_writer", None)
        if writer is not None:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ accounting

    def _mark_up(self) -> None:
        now = time.monotonic()
        if self.stats.disconnected_since is not None:
            self.stats.total_downtime_s += now - self.stats.disconnected_since
        self.stats.disconnected_since = None
        self.stats.connected_since = now
        self.stats.record_transition(True)

    def _mark_down(self) -> None:
        now = time.monotonic()
        if self.stats.connected_since is not None:
            self.stats.total_uptime_s += now - self.stats.connected_since
        self.stats.connected_since = None
        self.stats.disconnected_since = now
        self.stats.record_transition(False)

    def _set_state(self, state: ManagerState) -> None:
        if state is not self.state:
            old, self.state = self.state, state
            self._emit("state_change", detail=f"{old.value}->{state.value}")

    def _emit(self, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "event": event,
            "state": self.state.value,
            "host": self.host,
            **fields,
        }
        _LOGGER.debug("%s -- %s %s", self.host, event, fields or "")
        for cb in (self._event_cb, *self._listeners):
            if cb is None:
                continue
            try:
                cb(payload)
            except Exception:  # noqa: BLE001 - listeners must never kill us
                _LOGGER.exception("event listener failed")
