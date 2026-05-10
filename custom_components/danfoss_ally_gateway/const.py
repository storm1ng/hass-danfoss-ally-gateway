"""Constants for the Danfoss Ally Gateway integration."""

from __future__ import annotations

from typing import Final

# Integration domain
DOMAIN: Final = "danfoss_ally_gateway"

# ── Backend types ──────────────────────────────────────────────────────
BACKEND_Z2M: Final = "zigbee2mqtt"
BACKEND_ZHA: Final = "zha"

# ── Config keys ────────────────────────────────────────────────────────
CONF_BACKEND: Final = "backend"
CONF_MQTT_BASE_TOPIC: Final = "mqtt_base_topic"

# ── Platforms ──────────────────────────────────────────────────────────
PLATFORMS: Final = []
