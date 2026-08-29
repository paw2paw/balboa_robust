"""Fake SpaClient + SpaConnectionManager for unit tests.

The real manager owns a live TCP socket and a supervision task, both
unwanted in tests. FakeManager exposes the same public surface (attributes
and methods used by the coordinator / platforms) but does no I/O.

The coordinator's discovery fan-out is driven by events, so tests call
``fake.fire_config_loaded()`` to synchronously deliver a fake event that
triggers ``coordinator._maybe_fire_config_loaded``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import IntEnum
from typing import Any

from pybalboa.enums import (
    HeatState,
    OffOnState,
    SpaState,
    WiFiState,
)


class _MissingControl:
    """Sentinel raised by pybalboa properties when the control is absent."""


class FakeControl:
    """Enough of pybalboa.SpaControl for our entities."""

    def __init__(
        self,
        name: str,
        options: list[IntEnum] | None = None,
        state: IntEnum | None = None,
    ) -> None:
        self.name = name
        self.options = list(options or list(OffOnState))
        self._state = state if state is not None else self.options[0]
        self.set_state_calls: list[IntEnum] = []

    @property
    def state(self) -> IntEnum:
        return self._state

    async def set_state(self, opt: IntEnum) -> bool:
        self.set_state_calls.append(opt)
        self._state = opt
        return True


@dataclass
class FakeSpaClient:
    """A minimal SpaClient stand-in.

    Every attribute the integration reads must exist; anything expected to
    raise IndexError (heat_mode / temperature_range when absent) is
    represented by _MissingControl and a property below.
    """

    configuration_loaded: bool = True
    mac_address: str | None = "aa:bb:cc:dd:ee:ff"
    model: str | None = "TestSpa"
    software_version: str | None = "1.0"

    pumps: list[FakeControl] = field(default_factory=list)
    blowers: list[FakeControl] = field(default_factory=list)
    lights: list[FakeControl] = field(default_factory=list)
    aux: list[FakeControl] = field(default_factory=list)
    misters: list[FakeControl] = field(default_factory=list)

    heat_state: HeatState = HeatState.OFF
    voltage: int | None = 240
    state: SpaState = SpaState.RUNNING
    wifi_state: WiFiState | None = WiFiState.OK

    filter_cycle_1_start: time = time(6, 0)
    filter_cycle_1_end: time = time(8, 0)
    filter_cycle_1_duration: timedelta = timedelta(hours=2)
    filter_cycle_1_running: bool = False
    filter_cycle_2_enabled: bool = False
    filter_cycle_2_start: time = time(20, 0)
    filter_cycle_2_end: time = time(22, 0)
    filter_cycle_2_duration: timedelta = timedelta(hours=2)
    filter_cycle_2_running: bool = False

    circulation_pump: FakeControl | None = None

    _heat_mode_present: bool = True
    _temp_range_present: bool = True

    _time_offset: timedelta = timedelta(seconds=0)

    def get_current_time(self) -> datetime:
        return datetime.now() + self._time_offset

    async def request_fault_log(self) -> None:
        return None

    async def set_time(self, hour: int, minute: int, is_24_hour: bool | None = None) -> None:  # noqa: D401
        self.set_time_calls.append((hour, minute, is_24_hour))

    async def configure_filter_cycle(self, index: int, **kwargs: Any) -> None:
        self.configure_filter_calls.append((index, kwargs))

    # These properties emulate pybalboa's IndexError-raising behavior when
    # the underlying control isn't present.
    @property
    def heat_mode(self) -> FakeControl:
        if not self._heat_mode_present:
            raise IndexError("heat_mode absent")
        return self._heat_mode_ctrl

    @property
    def temperature_range(self) -> FakeControl:
        if not self._temp_range_present:
            raise IndexError("temperature_range absent")
        return self._temp_range_ctrl

    def __post_init__(self) -> None:
        self._heat_mode_ctrl = FakeControl("heat_mode")
        self._temp_range_ctrl = FakeControl("temperature_range")
        self.set_time_calls: list[tuple[int, int, bool | None]] = []
        self.configure_filter_calls: list[tuple[int, dict[str, Any]]] = []


class _Stats:
    connects_ok = 0
    connects_failed = 0
    connections_lost = 0
    last_connect_ms: float | None = None
    current_uptime_s = 0.0
    current_downtime_s = 0.0

    def uptime_ratio(self, _window: float) -> float | None:
        return None


class _State:
    value = "connected"


class _Config:
    stable_for = 0.0
    uptime_window = 3600.0


class FakeManager:
    """Stand-in for SpaConnectionManager — no sockets, no supervisor task."""

    last_instance: "FakeManager | None" = None

    def __init__(
        self,
        host: str,
        port: int,
        config: Any = None,
        event_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.config = _Config()
        self.state = _State()
        self.stats = _Stats()
        self.client: FakeSpaClient | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self.connected = False
        self.paused = False
        self.reachable = False
        self.next_attempt_at: datetime | None = None
        self.current_backoff_s = 0.0
        FakeManager.last_instance = self

    def add_listener(self, cb: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def apply_config(self, config: Any) -> None:
        self.config = _Config()

    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.paused = False

    def install_client(self, client: FakeSpaClient) -> None:
        """Attach a fake client and mark the link healthy."""
        self.client = client
        self.connected = True
        self.reachable = True

    def fire(self, event_name: str = "connect_ok") -> None:
        """Deliver a synthetic manager event to every subscriber."""
        payload = {"event": event_name}
        for cb in list(self._listeners):
            cb(payload)
