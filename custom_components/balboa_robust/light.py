"""Light entities for spa lights (on/off)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
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
    coordinator = entry.runtime_data

    @callback
    def _discover() -> None:
        client = coordinator.manager.client
        if client is None:
            return
        count = len(getattr(client, "lights", []) or [])
        if count:
            _LOGGER.info("balboa_robust: discovered %d light entities", count)
            async_add_entities(SpaLight(coordinator, i) for i in range(count))

    coordinator.on_first_config_loaded(_discover)


class SpaLight(CoordinatorEntity[SpaCoordinator], LightEntity):
    """A single spa light, on/off."""

    _attr_has_entity_name = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator: SpaCoordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_{index}"
        self._attr_device_info = coordinator.device_info

    def _control(self) -> Any | None:
        client = self.coordinator.manager.client
        if client is None:
            return None
        lights = list(getattr(client, "lights", []) or [])
        if self._index >= len(lights):
            return None
        return lights[self._index]

    @property
    def name(self) -> str | None:
        control = self._control()
        if control is None:
            return f"Light {self._index + 1}"
        return getattr(control, "name", None) or f"Light {self._index + 1}"

    @property
    def available(self) -> bool:
        return self.coordinator.manager.connected and self._control() is not None

    @property
    def is_on(self) -> bool | None:
        control = self._control()
        if control is None:
            return None
        value = getattr(control.state, "value", None)
        return value not in (None, 0)

    async def async_turn_on(self, **kwargs: Any) -> None:
        control = self._control()
        if control is None:
            return
        for opt in control.options:
            if getattr(opt, "value", 0) != 0:
                await control.set_state(opt)
                return

    async def async_turn_off(self, **kwargs: Any) -> None:
        control = self._control()
        if control is None:
            return
        for opt in control.options:
            if getattr(opt, "value", None) == 0:
                await control.set_state(opt)
                return
