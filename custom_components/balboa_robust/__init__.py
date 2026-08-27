"""Balboa Spa (Robust) — connection-resilient Balboa integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .connection import ManagerConfig, SpaConnectionManager
from .const import (
    CONF_AUTO_PAUSE_AFTER,
    CONF_BACKOFF_FACTOR,
    CONF_BACKOFF_INITIAL,
    CONF_BACKOFF_MAX,
    CONF_CONNECT_TIMEOUT,
    CONF_HEARTBEAT_INTERVAL,
    CONF_MAX_RETRIES,
    CONF_RECONNECT_ON_ERROR,
    CONF_STABLE_FOR,
    CONF_STALE_AFTER,
    CONF_UPTIME_WINDOW,
    DEFAULTS,
    DOMAIN,
    SERVICE_PAUSE,
    SERVICE_RESUME,
)
from .coordinator import SpaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.EVENT,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

SERVICE_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})

type BalboaRobustConfigEntry = ConfigEntry[SpaCoordinator]


def _manager_config(options: dict[str, Any]) -> ManagerConfig:
    """Translate config-entry options into a ManagerConfig."""

    def opt(key: str) -> Any:
        return options.get(key, DEFAULTS[key])

    return ManagerConfig(
        connect_timeout=float(opt(CONF_CONNECT_TIMEOUT)),
        backoff_initial=float(opt(CONF_BACKOFF_INITIAL)),
        backoff_max=float(opt(CONF_BACKOFF_MAX)),
        backoff_factor=float(opt(CONF_BACKOFF_FACTOR)),
        heartbeat_interval=float(opt(CONF_HEARTBEAT_INTERVAL)),
        stale_after=float(opt(CONF_STALE_AFTER)),
        max_retries=int(opt(CONF_MAX_RETRIES)),
        reconnect_on_error=bool(opt(CONF_RECONNECT_ON_ERROR)),
        auto_pause_after_failures=int(opt(CONF_AUTO_PAUSE_AFTER)),
        stable_for=float(opt(CONF_STABLE_FOR)),
        uptime_window=float(opt(CONF_UPTIME_WINDOW)),
    )


# (platform_domain, unique_id_suffix) pairs whose entities used to exist but
# were superseded — e.g. filter-cycle sensors replaced by editable Time
# entities, fault sensors replaced by an Event entity, and the redundant
# heat_mode/temperature_range sensors that duplicated the Select entities.
_OBSOLETE_ENTITIES: set[tuple[str, str]] = {
    ("sensor", "heat_mode"),
    ("sensor", "temperature_range"),
    ("sensor", "filter_cycle_1_start"),
    ("sensor", "filter_cycle_1_end"),
    ("sensor", "filter_cycle_1_duration_min"),
    ("sensor", "filter_cycle_2_start"),
    ("sensor", "filter_cycle_2_end"),
    ("sensor", "filter_cycle_2_duration_min"),
    ("sensor", "last_fault"),
    ("sensor", "last_fault_at"),
}


@callback
def _remove_obsolete_entities(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> None:
    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity_id, e in list(reg.entities.items()):
        if e.config_entry_id != entry.entry_id:
            continue
        if not e.unique_id.startswith(prefix):
            continue
        suffix = e.unique_id[len(prefix):]
        if (e.domain, suffix) in _OBSOLETE_ENTITIES:
            _LOGGER.info(
                "Removing obsolete balboa_robust entity %s (%s)",
                entity_id,
                e.unique_id,
            )
            reg.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> bool:
    """Set up a spa from a config entry."""
    _remove_obsolete_entities(hass, entry)
    manager = SpaConnectionManager(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        config=_manager_config(dict(entry.options)),
    )
    coordinator = SpaCoordinator(hass, entry, manager)
    entry.runtime_data = coordinator

    await manager.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    _register_services(hass)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.manager.stop()
    return unload_ok


async def _options_updated(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> None:
    """Hot-apply new options without a restart."""
    entry.runtime_data.manager.apply_config(_manager_config(dict(entry.options)))
    _LOGGER.info("Applied new connection options for %s", entry.data[CONF_HOST])


def _register_services(hass: HomeAssistant) -> None:
    """Register pause/resume services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_PAUSE):
        return

    def _managers(call: ServiceCall) -> list[SpaConnectionManager]:
        entry_id = call.data.get("entry_id")
        managers: list[SpaConnectionManager] = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry_id and entry.entry_id != entry_id:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            managers.append(coordinator.manager)
        return managers

    async def _pause(call: ServiceCall) -> None:
        for manager in _managers(call):
            await manager.pause()

    async def _resume(call: ServiceCall) -> None:
        for manager in _managers(call):
            await manager.resume()

    hass.services.async_register(DOMAIN, SERVICE_PAUSE, _pause, SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, _resume, SERVICE_SCHEMA)
