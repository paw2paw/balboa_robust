"""Shared fixtures for Balboa Robust tests.

Uses ``pytest-homeassistant-custom-component``'s helpers to spin up a full
Home Assistant instance per test with our integration loaded via HACS-style
discovery from ``custom_components/``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Iterator[None]:
    """Autoload ``custom_components/balboa_robust`` for every test."""
    yield


@pytest.fixture
def patch_manager():
    """Patch the network-touching SpaConnectionManager with a fake.

    Yields the FakeManager *class*; tests can set class-level attributes
    on the returned instance via ``FakeManager.last_instance`` after setup.
    """
    from tests.fakes import FakeManager

    with patch(
        "custom_components.balboa_robust.SpaConnectionManager", FakeManager
    ):
        yield FakeManager
