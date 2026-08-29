"""Capability-gate tests for aux / mister / wifi_state.

These verify that optional entities are only registered when the underlying
pybalboa capability is present, and are absent otherwise. The setup flow
patches ``SpaConnectionManager`` so no TCP is attempted; we install a fake
client and fire a synthetic ``connect_ok`` event to drive the coordinator's
first-config-loaded hook.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from custom_components.balboa_robust.const import DOMAIN
from tests.fakes import FakeControl, FakeManager, FakeSpaClient


async def _setup(
    hass: HomeAssistant,
    patch_manager: type[FakeManager],
    client: FakeSpaClient,
) -> MockConfigEntry:
    """Add a config entry, run setup, install the fake client, fire discovery."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "1.2.3.4", CONF_PORT: 4257},
        options={},
        title="Test Spa",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = patch_manager.last_instance
    assert manager is not None
    manager.install_client(client)
    manager.fire("connect_ok")
    await hass.async_block_till_done()
    return entry


def _unique_ids(hass: HomeAssistant, entry: MockConfigEntry, domain: str) -> set[str]:
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    return {
        e.unique_id[len(prefix):]
        for e in reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and e.domain == domain
        and e.unique_id.startswith(prefix)
    }


@pytest.mark.asyncio
async def test_aux_absent_no_switch(hass: HomeAssistant, patch_manager: Any) -> None:
    client = FakeSpaClient(aux=[], misters=[])
    entry = await _setup(hass, patch_manager, client)
    switch_ids = _unique_ids(hass, entry, "switch")
    assert not any(uid.startswith("aux_") for uid in switch_ids)


@pytest.mark.asyncio
async def test_aux_present_registers_switches(
    hass: HomeAssistant, patch_manager: Any
) -> None:
    client = FakeSpaClient(
        aux=[FakeControl("Aux 1"), FakeControl("Aux 2")],
        misters=[],
    )
    entry = await _setup(hass, patch_manager, client)
    switch_ids = _unique_ids(hass, entry, "switch")
    assert "aux_0" in switch_ids
    assert "aux_1" in switch_ids


@pytest.mark.asyncio
async def test_mister_absent_no_switch(
    hass: HomeAssistant, patch_manager: Any
) -> None:
    client = FakeSpaClient(misters=[])
    entry = await _setup(hass, patch_manager, client)
    switch_ids = _unique_ids(hass, entry, "switch")
    assert not any(uid.startswith("mister_") for uid in switch_ids)


@pytest.mark.asyncio
async def test_mister_present_registers_switch(
    hass: HomeAssistant, patch_manager: Any
) -> None:
    client = FakeSpaClient(misters=[FakeControl("Mister 1")])
    entry = await _setup(hass, patch_manager, client)
    switch_ids = _unique_ids(hass, entry, "switch")
    assert "mister_0" in switch_ids


@pytest.mark.asyncio
async def test_wifi_state_none_no_sensor(
    hass: HomeAssistant, patch_manager: Any
) -> None:
    client = FakeSpaClient(wifi_state=None)
    entry = await _setup(hass, patch_manager, client)
    sensor_ids = _unique_ids(hass, entry, "sensor")
    assert "wifi_state" not in sensor_ids


@pytest.mark.asyncio
async def test_wifi_state_present_registers_sensor(
    hass: HomeAssistant, patch_manager: Any
) -> None:
    from pybalboa.enums import WiFiState

    client = FakeSpaClient(wifi_state=WiFiState.OK)
    entry = await _setup(hass, patch_manager, client)
    sensor_ids = _unique_ids(hass, entry, "sensor")
    assert "wifi_state" in sensor_ids
