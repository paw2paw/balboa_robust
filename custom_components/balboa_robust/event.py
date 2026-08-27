"""Spa fault as a native Home Assistant Event entity.

Matches the stock balboa integration's shape: one entity, event_type is a
symbolic name for the fault code (e.g. "flow_failed"). The last-fault code
+ ISO timestamp are exposed as attributes, and HA renders it as a live log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BalboaRobustConfigEntry
from .coordinator import SpaCoordinator

_LOGGER = logging.getLogger(__name__)

REQUEST_FAULT_LOG_INTERVAL = timedelta(minutes=5)

# Mirrors stock balboa integration so users get identical event_type names.
FAULT_MESSAGE_CODE_MAP: dict[int, str] = {
    15: "sensor_out_of_sync",
    16: "low_flow",
    17: "flow_failed",
    18: "settings_reset",
    19: "priming_mode",
    20: "clock_failed",
    21: "settings_reset",
    22: "memory_failure",
    26: "service_sensor_sync",
    27: "heater_dry",
    28: "heater_may_be_dry",
    29: "water_too_hot",
    30: "heater_too_hot",
    31: "sensor_a_fault",
    32: "sensor_b_fault",
    34: "pump_stuck",
    35: "hot_fault",
    36: "gfci_test_failed",
    37: "standby_mode",
}
FAULT_EVENT_TYPES = sorted(set(FAULT_MESSAGE_CODE_MAP.values()))

FAULT_DATE = "fault_date"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalboaRobustConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SpaFaultEvent(entry.runtime_data)])


class SpaFaultEvent(CoordinatorEntity[SpaCoordinator], EventEntity):
    """Emits an HA event every time a new spa fault code arrives."""

    _attr_has_entity_name = True
    _attr_translation_key = "fault"
    _attr_event_types = FAULT_EVENT_TYPES

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_event_fault"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        # Event history is meaningful even when disconnected.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        async def _poll(_now: datetime | None = None) -> None:
            client = self.coordinator.manager.client
            request = getattr(client, "request_fault_log", None) if client else None
            if request is None:
                return
            try:
                await request()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("request_fault_log failed", exc_info=True)

        # One eager request now, then every 5 minutes.
        await _poll()
        self.async_on_remove(
            async_track_time_interval(self.hass, _poll, REQUEST_FAULT_LOG_INTERVAL)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        client = self.coordinator.manager.client
        fault = getattr(client, "fault", None) if client else None
        if fault is None:
            super()._handle_coordinator_update()
            return
        fault_date = fault.fault_datetime.isoformat()
        if self.state_attributes.get(FAULT_DATE) != fault_date:
            event_type = FAULT_MESSAGE_CODE_MAP.get(
                fault.message_code, fault.message or f"code_{fault.message_code}"
            )
            self._trigger_event(
                event_type,
                {FAULT_DATE: fault_date, "code": fault.message_code},
            )
        super()._handle_coordinator_update()
