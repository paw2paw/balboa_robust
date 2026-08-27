"""Binary sensors: reachability + spa-side runtime flags.

`binary_sensor.spa_reachable` is ON only when the connection has been healthy
(heartbeat-confirmed) for `stable_for` seconds. Automations should gate spa
commands on this so they don't fire into a flapping/stale link:

    condition:
      - condition: state
        entity_id: binary_sensor.spa_reachable
        state: "on"

For "run when it comes back" automations, trigger on the state change with a
debounce instead of a bespoke event:

    trigger:
      - platform: state
        entity_id: binary_sensor.spa_reachable
        to: "on"
        for: "00:00:30"
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BalboaRobustConfigEntry
from .coordinator import SpaCoordinator


@dataclass(frozen=True, kw_only=True)
class SpaBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with a value extractor over the SpaClient."""

    value_fn: Callable[[Any], bool | None]


SPA_BINARIES: tuple[SpaBinaryDescription, ...] = (
    SpaBinaryDescription(
        key="filter_cycle_1_running",
        translation_key="filter_cycle_1_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: bool(getattr(c, "filter_cycle_1_running", False)),
    ),
    SpaBinaryDescription(
        key="filter_cycle_2_running",
        translation_key="filter_cycle_2_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: bool(getattr(c, "filter_cycle_2_running", False)),
    ),
    SpaBinaryDescription(
        key="circulation_pump_running",
        translation_key="circulation_pump_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: _circ_running(c),
    ),
)


def _circ_running(client: Any) -> bool | None:
    ctrl = getattr(client, "circulation_pump", None)
    if ctrl is None:
        return None
    state = getattr(ctrl, "state", None)
    value = getattr(state, "value", None)
    if value is None:
        return None
    return value != 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalboaRobustConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([SpaReachableBinarySensor(coordinator)])
    async_add_entities(
        SpaClientBinarySensor(coordinator, d) for d in SPA_BINARIES
    )


class SpaReachableBinarySensor(
    CoordinatorEntity[SpaCoordinator], BinarySensorEntity
):
    """Reachability gate for spa automations."""

    _attr_has_entity_name = True
    _attr_translation_key = "reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reachable"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        # The reachability sensor itself is always available — its whole job
        # is to report on the spa, including when the spa is unreachable.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.manager.reachable


class SpaClientBinarySensor(
    CoordinatorEntity[SpaCoordinator], BinarySensorEntity
):
    """Binary sensor backed by a value on the pybalboa SpaClient."""

    _attr_has_entity_name = True
    entity_description: SpaBinaryDescription

    def __init__(
        self,
        coordinator: SpaCoordinator,
        description: SpaBinaryDescription,
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
    def is_on(self) -> bool | None:
        client = self.coordinator.manager.client
        if client is None:
            return None
        return self.entity_description.value_fn(client)
