"""Sensors: SpaConnectionManager diagnostics + spa-side state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BalboaRobustConfigEntry
from .connection import SpaConnectionManager
from .coordinator import SpaCoordinator


@dataclass(frozen=True, kw_only=True)
class SpaSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[SpaConnectionManager], Any]


SENSORS: tuple[SpaSensorDescription, ...] = (
    SpaSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=[
            "disconnected",
            "connecting",
            "connected",
            "backoff",
            "paused",
            "stopped",
        ],
        value_fn=lambda m: m.state.value,
    ),
    SpaSensorDescription(
        key="connect_latency",
        translation_key="connect_latency",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="ms",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.stats.last_connect_ms,
    ),
    SpaSensorDescription(
        key="connects_ok",
        translation_key="connects_ok",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda m: m.stats.connects_ok,
    ),
    SpaSensorDescription(
        key="connects_failed",
        translation_key="connects_failed",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda m: m.stats.connects_failed,
    ),
    SpaSensorDescription(
        key="connections_lost",
        translation_key="connections_lost",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda m: m.stats.connections_lost,
    ),
    SpaSensorDescription(
        key="current_uptime",
        translation_key="current_uptime",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: round(m.stats.current_uptime_s),
    ),
    SpaSensorDescription(
        key="current_downtime",
        translation_key="current_downtime",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: round(m.stats.current_downtime_s),
    ),
    SpaSensorDescription(
        key="current_backoff",
        translation_key="current_backoff",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: round(m.current_backoff_s, 1),
    ),
    SpaSensorDescription(
        key="next_attempt_at",
        translation_key="next_attempt_at",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: m.next_attempt_at,
    ),
    SpaSensorDescription(
        key="uptime_ratio",
        translation_key="uptime_ratio",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: (
            round(r * 100, 1)
            if (r := m.stats.uptime_ratio(m.config.uptime_window)) is not None
            else None
        ),
    ),
)


@dataclass(frozen=True, kw_only=True)
class SpaClientSensorDescription(SensorEntityDescription):
    """Sensor description that pulls from the pybalboa SpaClient."""

    value_fn: Callable[[Any], Any]
    attributes_fn: Callable[[Any], dict[str, Any]] | None = None


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    return name.lower() if name else None


SPA_SENSORS: tuple[SpaClientSensorDescription, ...] = (
    SpaClientSensorDescription(
        key="heat_state",
        translation_key="heat_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["off", "heating", "heat_waiting"],
        value_fn=lambda c: _enum_name(getattr(c, "heat_state", None)),
    ),
    SpaClientSensorDescription(
        key="voltage",
        translation_key="voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: getattr(c, "voltage", None),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalboaRobustConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SpaDiagnosticSensor(coordinator, description) for description in SENSORS
    )
    async_add_entities(
        SpaClientSensor(coordinator, description) for description in SPA_SENSORS
    )


class SpaDiagnosticSensor(CoordinatorEntity[SpaCoordinator], SensorEntity):
    """A diagnostic sensor backed by the connection manager."""

    _attr_has_entity_name = True
    entity_description: SpaSensorDescription

    def __init__(
        self, coordinator: SpaCoordinator, description: SpaSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True  # diagnostics stay visible even while spa is offline

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.manager)


class SpaClientSensor(CoordinatorEntity[SpaCoordinator], SensorEntity):
    """A sensor backed by an attribute on the pybalboa SpaClient."""

    _attr_has_entity_name = True
    entity_description: SpaClientSensorDescription

    def __init__(
        self,
        coordinator: SpaCoordinator,
        description: SpaClientSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return (
            self.coordinator.manager.connected
            and self.coordinator.manager.client is not None
        )

    @property
    def native_value(self) -> Any:
        client = self.coordinator.manager.client
        if client is None:
            return None
        try:
            return self.entity_description.value_fn(client)
        except Exception:  # noqa: BLE001
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        client = self.coordinator.manager.client
        fn = self.entity_description.attributes_fn
        if client is None or fn is None:
            return None
        try:
            attrs = fn(client)
        except Exception:  # noqa: BLE001
            return None
        return attrs or None
