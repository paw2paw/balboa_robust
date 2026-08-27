"""Fan entities for spa pumps and blowers.

pybalboa exposes each pump/blower as a SpaControl whose ``options`` list is
either [OFF, ON] (single-speed) or [OFF, LOW, HIGH] (two-speed). We map those
to HA preset modes so the user gets a native speed picker without us guessing.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
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
        entities: list[SpaFan] = []
        for index in range(len(getattr(client, "pumps", []) or [])):
            entities.append(SpaFan(coordinator, "pump", index))
        for index in range(len(getattr(client, "blowers", []) or [])):
            entities.append(SpaFan(coordinator, "blower", index))
        if entities:
            _LOGGER.info(
                "balboa_robust: discovered %d fan entities (pumps+blowers)",
                len(entities),
            )
            async_add_entities(entities)

    coordinator.on_first_config_loaded(_discover)


class SpaFan(CoordinatorEntity[SpaCoordinator], FanEntity):
    """A pump or blower. Preset modes reflect the control's own option list."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: SpaCoordinator, kind: str, index: int) -> None:
        super().__init__(coordinator)
        self._kind = kind  # "pump" | "blower"
        self._index = index
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{kind}_{index}"
        self._attr_device_info = coordinator.device_info

    def _controls(self) -> list[Any]:
        client = self.coordinator.manager.client
        if client is None:
            return []
        attr = "pumps" if self._kind == "pump" else "blowers"
        return list(getattr(client, attr, []) or [])

    def _control(self) -> Any | None:
        controls = self._controls()
        if self._index >= len(controls):
            return None
        return controls[self._index]

    @property
    def name(self) -> str | None:
        control = self._control()
        if control is None:
            return f"{self._kind.capitalize()} {self._index + 1}"
        return getattr(control, "name", None) or f"{self._kind.capitalize()} {self._index + 1}"

    @property
    def available(self) -> bool:
        return self.coordinator.manager.connected and self._control() is not None

    @property
    def preset_modes(self) -> list[str] | None:
        control = self._control()
        if control is None:
            return None
        return [_opt_name(opt) for opt in control.options]

    @property
    def preset_mode(self) -> str | None:
        control = self._control()
        if control is None:
            return None
        return _opt_name(control.state)

    @property
    def is_on(self) -> bool | None:
        control = self._control()
        if control is None:
            return None
        value = getattr(control.state, "value", None)
        return value not in (None, 0)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        control = self._control()
        if control is None:
            return
        for opt in control.options:
            if _opt_name(opt) == preset_mode.lower():
                await control.set_state(opt)
                return
        _LOGGER.warning(
            "Unknown preset mode %s for %s (options: %s)",
            preset_mode,
            self.name,
            [_opt_name(o) for o in control.options],
        )

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        control = self._control()
        if control is None:
            return
        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
            return
        # Default "on" = highest available non-off option (HIGH for two-speed, ON for one-speed).
        non_off = [opt for opt in control.options if getattr(opt, "value", 0) != 0]
        if non_off:
            await control.set_state(non_off[-1])

    async def async_turn_off(self, **kwargs: Any) -> None:
        control = self._control()
        if control is None:
            return
        for opt in control.options:
            if getattr(opt, "value", None) == 0:
                await control.set_state(opt)
                return


def _opt_name(opt: IntEnum | Any) -> str:
    """Lowercase enum name suitable for HA preset_mode strings."""
    name = getattr(opt, "name", None)
    return name.lower() if name else str(opt).lower()
