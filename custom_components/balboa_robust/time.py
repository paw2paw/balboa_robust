"""Editable filter-cycle start/end times.

Same shape as the stock balboa integration: one entity per (cycle, period)
in Configuration. Writes go through ``configure_filter_cycle`` which the
module echoes back, so the reported value stays authoritative.
"""

from __future__ import annotations

import itertools
import logging
from datetime import time as dtime
from typing import Any

from homeassistant.components.time import TimeEntity
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
        entities = [
            SpaFilterCycleTime(coordinator, index, period)
            for index, period in itertools.product((1, 2), ("start", "end"))
        ]
        async_add_entities(entities)

    coordinator.on_first_config_loaded(_discover)


class SpaFilterCycleTime(CoordinatorEntity[SpaCoordinator], TimeEntity):
    """A single (cycle, start|end) time editable from the device page."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SpaCoordinator, index: int, period: str
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._period = period
        self._attr_translation_key = f"filter_cycle_{period}"
        self._attr_translation_placeholders = {"index": str(index)}
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_time_filter_cycle_{index}_{period}"
        )
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return (
            self.coordinator.manager.connected
            and self.coordinator.manager.client is not None
        )

    @property
    def native_value(self) -> dtime | None:
        client = self.coordinator.manager.client
        if client is None:
            return None
        return getattr(client, f"filter_cycle_{self._index}_{self._period}", None)

    async def async_set_value(self, value: dtime) -> None:
        client = self.coordinator.manager.client
        if client is None:
            _LOGGER.warning("Cannot set filter cycle time: spa not connected")
            return
        kwargs: dict[str, Any] = {self._period: value}
        await client.configure_filter_cycle(self._index, **kwargs)
