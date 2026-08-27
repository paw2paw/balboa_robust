"""Switches: connection pause + All Pumps master."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
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
    coordinator = entry.runtime_data
    async_add_entities([SpaPauseSwitch(coordinator), SpaAllPumpsSwitch(coordinator)])
    coordinator.on_first_config_loaded(
        lambda: async_add_entities([SpaFilterCycle2EnabledSwitch(coordinator)])
    )


class SpaPauseSwitch(CoordinatorEntity[SpaCoordinator], SwitchEntity):
    """Toggle pause on the connection manager.

    Always available — you must be able to un-pause even while offline.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "pause"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:pause-octagon"

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_pause"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.manager.paused

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.manager.pause()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.manager.resume()


class SpaAllPumpsSwitch(CoordinatorEntity[SpaCoordinator], SwitchEntity):
    """Master switch for every pump on the spa.

    ON = drive every pump to its highest non-off option (HIGH for two-speed,
    ON for single-speed). OFF = every pump to OFF. Reports ON when any pump
    is running so partial states remain visible.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "all_pumps"
    _attr_name = "All pumps"
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_all_pumps"
        self._attr_device_info = coordinator.device_info

    def _pumps(self) -> list[Any]:
        client = self.coordinator.manager.client
        if client is None:
            return []
        return list(getattr(client, "pumps", []) or [])

    @property
    def available(self) -> bool:
        return self.coordinator.manager.connected and bool(self._pumps())

    @property
    def is_on(self) -> bool | None:
        pumps = self._pumps()
        if not pumps:
            return None
        return any(getattr(p.state, "value", 0) not in (None, 0) for p in pumps)

    async def async_turn_on(self, **kwargs: Any) -> None:
        for pump in self._pumps():
            non_off = [opt for opt in pump.options if getattr(opt, "value", 0) != 0]
            if not non_off:
                continue
            try:
                await pump.set_state(non_off[-1])
            except Exception:  # noqa: BLE001
                _LOGGER.exception("All-pumps ON failed for %s", pump)

    async def async_turn_off(self, **kwargs: Any) -> None:
        for pump in self._pumps():
            off_opt = next(
                (opt for opt in pump.options if getattr(opt, "value", None) == 0),
                None,
            )
            if off_opt is None:
                continue
            try:
                await pump.set_state(off_opt)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("All-pumps OFF failed for %s", pump)


class SpaFilterCycle2EnabledSwitch(
    CoordinatorEntity[SpaCoordinator], SwitchEntity
):
    """Enable/disable filter cycle 2 (cycle 1 is always on)."""

    _attr_has_entity_name = True
    _attr_translation_key = "filter_cycle_2_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:filter-cog"

    def __init__(self, coordinator: SpaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_filter_cycle_2_enabled"
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
        return bool(getattr(client, "filter_cycle_2_enabled", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        client = self.coordinator.manager.client
        if client is None:
            return
        await client.configure_filter_cycle(2, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        client = self.coordinator.manager.client
        if client is None:
            return
        await client.configure_filter_cycle(2, enabled=False)
