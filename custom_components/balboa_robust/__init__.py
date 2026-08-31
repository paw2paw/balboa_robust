"""Balboa Spa (Robust) — connection-resilient Balboa integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EntityCategory, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

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
    SERVICE_SYNC_SPA_CLOCK,
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

# (platform_domain, unique_id_suffix) -> new category. Used to move
# already-registered entities that shipped in an earlier release with the
# wrong category. HA fixes the "original" category on registration but never
# retroactively updates existing rows, so we do it explicitly.
_RECATEGORIZE: dict[tuple[str, str], EntityCategory | None] = {
    ("sensor", "heat_state"): None,  # v0.2.5: was DIAGNOSTIC, now Sensors
    ("sensor", "voltage"): None,     # v0.2.5: was DIAGNOSTIC, now Sensors
    ("switch", "pause"): EntityCategory.DIAGNOSTIC,  # was CONFIG
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


@callback
def _migrate_entity_categories(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> None:
    """Move entities to their current default category if unchanged since last release."""
    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity_id, e in list(reg.entities.items()):
        if e.config_entry_id != entry.entry_id:
            continue
        if not e.unique_id.startswith(prefix):
            continue
        suffix = e.unique_id[len(prefix):]
        target = _RECATEGORIZE.get((e.domain, suffix), "unset")
        if target == "unset":
            continue
        if e.entity_category == target:
            continue
        _LOGGER.info(
            "Recategorizing %s: %s -> %s",
            entity_id,
            e.entity_category,
            target,
        )
        reg.async_update_entity(entity_id, entity_category=target)


@callback
def _migrate_climate_entity_id(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> None:
    """Rename the climate entity created before v0.3.5.

    v0.3.5 sets `_attr_name = "Thermostat"`, giving new installs the clean
    `climate.<device>_thermostat` slug. Earlier releases had
    `_attr_name = None` which HA turned into `climate.<device>_<device>`
    (or `climate.<device>` in some configurations). Only touched when the
    user hasn't manually renamed the entity — respects `has_entity_name`.
    """
    reg = er.async_get(hass)
    unique = f"{entry.entry_id}_climate"
    existing = reg.async_get_entity_id("climate", DOMAIN, unique)
    if existing is None:
        return
    device_slug = slugify(entry.title or entry.data.get(CONF_HOST, ""))
    if not device_slug:
        return
    target = f"climate.{device_slug}_thermostat"
    if existing == target:
        return
    if reg.async_get(target) is not None:
        _LOGGER.warning(
            "Cannot migrate %s -> %s: target already exists", existing, target
        )
        return
    _LOGGER.info("Migrating climate entity_id: %s -> %s", existing, target)
    reg.async_update_entity(existing, new_entity_id=target)


async def async_setup_entry(
    hass: HomeAssistant, entry: BalboaRobustConfigEntry
) -> bool:
    """Set up a spa from a config entry."""
    _remove_obsolete_entities(hass, entry)
    _migrate_entity_categories(hass, entry)
    _migrate_climate_entity_id(hass, entry)
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
    """Register pause/resume/sync-clock services (idempotent)."""
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

    async def _sync_clock(call: ServiceCall) -> None:
        now = datetime.now()
        for manager in _managers(call):
            client = manager.client
            if client is None or not manager.connected:
                _LOGGER.warning(
                    "sync_spa_clock: skipping %s (not connected)", manager
                )
                continue
            try:
                await client.set_time(now.hour, now.minute)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("sync_spa_clock failed")

    hass.services.async_register(DOMAIN, SERVICE_PAUSE, _pause, SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RESUME, _resume, SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_SPA_CLOCK, _sync_clock, SERVICE_SCHEMA
    )
