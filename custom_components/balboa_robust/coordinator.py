"""Coordinator bridging SpaConnectionManager events into Home Assistant."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .connection import SpaConnectionManager
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SpaCoordinator(DataUpdateCoordinator[None]):
    """Push-first coordinator: manager events trigger entity updates.

    The periodic tick is only a safety net so diagnostic sensors (uptime,
    downtime) keep counting even when the manager is silent in BACKOFF.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        manager: SpaConnectionManager,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=15),
        )
        self.entry = entry
        self.manager = manager
        self.last_event: dict[str, Any] | None = None
        self._config_loaded_seen = False
        self._config_loaded_callbacks: list[Callable[[], None]] = []
        manager.add_listener(self._handle_manager_event)

    async def _async_update_data(self) -> None:
        return None

    @callback
    def _handle_manager_event(self, event: dict[str, Any]) -> None:
        self.last_event = event
        self.async_update_listeners()

        # `reachable` becomes true stable_for seconds AFTER connect_ok, but no
        # manager event fires at that instant. Schedule a one-shot refresh so
        # binary_sensor.spa_reachable flips promptly instead of waiting for the
        # 15s safety-net tick.
        if event.get("event") == "connect_ok":
            delay = self.manager.config.stable_for + 0.5
            self.hass.loop.call_later(
                delay,
                lambda: self.hass.add_job(self.async_update_listeners),
            )

        self._maybe_fire_config_loaded()

    @callback
    def _maybe_fire_config_loaded(self) -> None:
        if self._config_loaded_seen:
            return
        client = self.manager.client
        if client is None or not getattr(client, "configuration_loaded", False):
            return
        self._config_loaded_seen = True
        callbacks, self._config_loaded_callbacks = self._config_loaded_callbacks, []
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("on_first_config_loaded callback failed")
        # Fault log isn't pushed automatically; ask for it so the sensor populates.
        request = getattr(client, "request_fault_log", None)
        if request is not None:
            self.hass.async_create_task(_safe_request(request))

    @callback
    def on_first_config_loaded(self, cb: Callable[[], None]) -> None:
        """Run cb the first time the client reports configuration_loaded.

        If it's already loaded (late subscriber), fires immediately.
        """
        if self._config_loaded_seen:
            cb()
            return
        self._config_loaded_callbacks.append(cb)

    @property
    def device_info(self) -> DeviceInfo:
        client = self.manager.client
        mac = getattr(client, "mac_address", None) if client else None
        info: DeviceInfo = DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="Balboa Water Group",
            model=getattr(client, "model", None) or "Spa",
            sw_version=getattr(client, "software_version", None),
        )
        if mac:
            info["connections"] = {("mac", str(mac))}
        return info


async def _safe_request(request: Any) -> None:
    """Best-effort call to a pybalboa request_* coroutine."""
    try:
        await request()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("pybalboa request failed", exc_info=True)
