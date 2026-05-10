"""Shared test fixtures for Danfoss Ally Gateway tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


def make_config_entry_data(
    backend: str = "zigbee2mqtt",
    mqtt_base_topic: str = "zigbee2mqtt",
) -> dict[str, Any]:
    """Build config entry data dict."""
    data: dict[str, Any] = {"backend": backend}
    if backend == "zigbee2mqtt":
        data["mqtt_base_topic"] = mqtt_base_topic
    return data
