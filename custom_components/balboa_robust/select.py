"""Select entities for user-controllable enum spa settings.

Wraps ``heat_mode`` (READY / REST) and ``temperature_range`` (LOW / HIGH).
Options come from the SpaControl's own ``options`` list so we mirror whatever
the module actually exposes rather than hard-coding.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
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
        entities: list[SpaControlSelect] = []
        if getattr(client, "heat_mode", None) is not None:
            entities.append(
                SpaControlSelect(
                    coordinator,
                    attr="heat_mode",
                    name="Heat mode",
                    icon="mdi:thermostat",
                    category=EntityCategory.CONFIG,
                )
            )
        try:
            if getattr(client, "temperature_range", None) is not None:
                entities.append(
                    SpaControlSelect(
                        coordinator,
                        attr="temperature_range",
                        name="Temperature range",
                        icon="mdi:thermometer-lines",
                    )
                )
        except IndexError:
            # temperature_range accessor raises if the control isn't present yet.
            pass
        if entities:
            _LOGGER.info("balboa_robust: discovered %d select entities", len(entities))
            async_add_entities(entities)

    coordinator.on_first_config_loaded(_discover)


class SpaControlSelect(CoordinatorEntity[SpaCoordinator], SelectEntity):
    """A SelectEntity backed by a SpaControl whose options are IntEnums."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SpaCoordinator,
        attr: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr = attr
        self._attr_name = name
        self._attr_icon = icon
        self._attr_translation_key = attr
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{attr}"
        self._attr_device_info = coordinator.device_info

    def _control(self) -> Any | None:
        client = self.coordinator.manager.client
        if client is None:
            return None
        try:
            return getattr(client, self._attr, None)
        except IndexError:
            return None

    @property
    def available(self) -> bool:
        return self.coordinator.manager.connected and self._control() is not None

    @property
    def options(self) -> list[str]:
        control = self._control()
        if control is None:
            return []
        return [_opt_name(opt) for opt in control.options]

    @property
    def current_option(self) -> str | None:
        control = self._control()
        if control is None:
            return None
        return _opt_name(control.state)

    async def async_select_option(self, option: str) -> None:
        control = self._control()
        if control is None:
            return
        for opt in control.options:
            if _opt_name(opt) == option.lower():
                await control.set_state(opt)
                return
        _LOGGER.warning(
            "Unknown option %s for %s (available: %s)",
            option,
            self._attr,
            self.options,
        )


def _opt_name(opt: IntEnum | Any) -> str:
    name = getattr(opt, "name", None)
    return name.lower() if name else str(opt).lower()
