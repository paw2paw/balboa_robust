"""Climate entity for the Balboa spa heater."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BalboaRobustConfigEntry
from .coordinator import SpaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BalboaRobustConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SpaClimate(entry.runtime_data)])


class SpaClimate(CoordinatorEntity[SpaCoordinator], ClimateEntity):
    """Thermostat for the spa heater. Unavailable while disconnected/paused."""

    _attr_has_entity_name = True
    _attr_name = "Thermostat"
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_translation_key = "thermostat"

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_climate"
        self._attr_device_info = coordinator.device_info

    @property
    def _client(self) -> Any:
        return self.coordinator.manager.client

    @property
    def available(self) -> bool:
        return self.coordinator.manager.connected and self._client is not None

    @property
    def temperature_unit(self) -> str:
        unit = getattr(self._client, "temperature_unit", None)
        # pybalboa TemperatureUnit: CELSIUS = 1, FAHRENHEIT = 0
        if unit is not None and getattr(unit, "name", "") == "FAHRENHEIT":
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float | None:
        return getattr(self._client, "temperature", None)

    @property
    def target_temperature(self) -> float | None:
        return getattr(self._client, "target_temperature", None)

    @property
    def min_temp(self) -> float:
        value = getattr(self._client, "temperature_minimum", None)
        return value if value is not None else super().min_temp

    @property
    def max_temp(self) -> float:
        value = getattr(self._client, "temperature_maximum", None)
        return value if value is not None else super().max_temp

    @property
    def hvac_action(self) -> HVACAction | None:
        heat_state = getattr(self._client, "heat_state", None)
        if heat_state is None:
            return None
        return (
            HVACAction.HEATING
            if getattr(heat_state, "name", "") == "HEATING"
            else HVACAction.IDLE
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        client = self._client
        if client is None:
            _LOGGER.warning("Cannot set temperature: spa not connected")
            return
        await client.set_temperature(float(temperature))
