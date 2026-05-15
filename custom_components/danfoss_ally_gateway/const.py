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

# ── Room config keys ───────────────────────────────────────────────────
CONF_ROOM_NAME: Final = "room_name"
CONF_TRV_ENTITIES: Final = "trv_entities"

# ── Availability ───────────────────────────────────────────────────────
TRV_AVAILABILITY_TIMEOUT: Final = 2 * 60 * 60  # 2 hours without update = unavailable

# ── Window open detection states ───────────────────────────────────────
WINDOW_OPEN_DETECTED: Final = 3

# ── Special values ─────────────────────────────────────────────────────
EXTERNAL_TEMP_DISABLED: Final = -8000  # Value to send to disable external temp

# ── Setpoint command types ─────────────────────────────────────────────
SETPOINT_TYPE_USER: Final = 1  # Aggressive motor response (manual dial change)

# ── Z2M attribute name mappings ────────────────────────────────────────
Z2M_ATTR_EXTERNAL_MEASURED_ROOM_SENSOR: Final = "external_measured_room_sensor"
Z2M_ATTR_HEAT_AVAILABLE: Final = "heat_available"
Z2M_ATTR_HEAT_REQUIRED: Final = "heat_required"
Z2M_ATTR_LOAD_BALANCING_ENABLE: Final = "load_balancing_enable"
Z2M_ATTR_LOAD_ROOM_MEAN: Final = "load_room_mean"
Z2M_ATTR_LOAD_ESTIMATE: Final = "load_estimate"
Z2M_ATTR_PREHEAT_STATUS: Final = "preheat_status"
Z2M_ATTR_PREHEAT_TIME: Final = "preheat_time"
Z2M_ATTR_WINDOW_OPEN_DETECTION: Final = "window_open_internal"
Z2M_ATTR_EXTERNAL_WINDOW_OPEN: Final = "window_open_external"
Z2M_ATTR_RADIATOR_COVERED: Final = "radiator_covered"
Z2M_ATTR_SETPOINT_CHANGE_SOURCE: Final = "setpoint_change_source"
Z2M_ATTR_PI_HEATING_DEMAND: Final = "pi_heating_demand"
Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: Final = "occupied_heating_setpoint"
Z2M_ATTR_LOCAL_TEMPERATURE: Final = "local_temperature"
Z2M_ATTR_PROGRAMMING_MODE: Final = "programming_operation_mode"

# ── Platforms ──────────────────────────────────────────────────────────
PLATFORMS: Final = []
