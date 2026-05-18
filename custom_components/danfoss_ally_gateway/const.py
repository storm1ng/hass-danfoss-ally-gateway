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
CONF_AREA: Final = "area"
CONF_TRV_ENTITIES: Final = "trv_entities"
CONF_TEMP_SENSOR: Final = "temperature_sensor"
CONF_HEAT_SOURCE: Final = "heat_source"
CONF_HEAT_SOURCE_TYPE: Final = "heat_source_type"
CONF_REMOTE_CLIMATE: Final = "remote_climate"

HEAT_SOURCE_CLIMATE: Final = "climate"
HEAT_SOURCE_BINARY_SENSOR: Final = "binary_sensor"

# ── Subentry types ─────────────────────────────────────────────────────
SUBENTRY_ROOM: Final = "room"

# ── Availability ───────────────────────────────────────────────────────
TRV_AVAILABILITY_TIMEOUT: Final = 2 * 60 * 60  # 2 hours without update = unavailable

# ── Window open detection states ───────────────────────────────────────
WINDOW_OPEN_DETECTED: Final = 3
WINDOW_OPEN_EXTERNAL_OPEN: Final = 4

# ── Special values ─────────────────────────────────────────────────────
EXTERNAL_TEMP_DISABLED: Final = -8000  # Value to send to disable external temp

# ── Timing constants (seconds) ────────────────────────────────────────
# External temperature forwarding - exposed mode (radiator_covered=false)
EXT_TEMP_EXPOSED_MIN_INTERVAL: Final = 30 * 60  # 30 minutes
EXT_TEMP_EXPOSED_MAX_INTERVAL: Final = 3 * 60 * 60  # 3 hours

# External temperature forwarding - covered mode (radiator_covered=true)
EXT_TEMP_COVERED_MIN_INTERVAL: Final = 5 * 60  # 5 minutes
EXT_TEMP_COVERED_MAX_INTERVAL: Final = 30 * 60  # 30 minutes

# Temperature change threshold for immediate send
EXT_TEMP_CHANGE_THRESHOLD: Final = 0.1  # Kelvin / degrees C

# Load balancing
LOAD_BALANCE_INTERVAL: Final = 15 * 60  # 15 minutes
LOAD_BALANCE_MAX_AGE: Final = 90 * 60  # 90 minutes
LOAD_BALANCE_INVALID_THRESHOLD: Final = -500
LOAD_BALANCE_DISABLED_VALUE: Final = -8000

# ── Remote climate sync ────────────────────────────────────────────────
REMOTE_CLIMATE_SUPPRESS_SECONDS: Final = 3.0  # Anti-echo suppression window

# ── Time sync ──────────────────────────────────────────────────────────
TIME_SYNC_INTERVAL: Final = 7 * 24 * 60 * 60  # Weekly

# ── Schedule validation ────────────────────────────────────────────────
SCHEDULE_MAX_DAILY_TRANSITIONS: Final = 6
SCHEDULE_MAX_WEEKLY_TRANSITIONS: Final = 42  # 7 × 6
SCHEDULE_MINUTES_PER_DAY: Final = 1440  # 24 × 60

# ── Schedule ZCL constants ─────────────────────────────────────────────
SCHEDULE_DOW_SUNDAY: Final = 0x01
SCHEDULE_DOW_MONDAY: Final = 0x02
SCHEDULE_DOW_TUESDAY: Final = 0x04
SCHEDULE_DOW_WEDNESDAY: Final = 0x08
SCHEDULE_DOW_THURSDAY: Final = 0x10
SCHEDULE_DOW_FRIDAY: Final = 0x20
SCHEDULE_DOW_SATURDAY: Final = 0x40

SCHEDULE_DOW_ALL: Final = [
    SCHEDULE_DOW_SUNDAY,
    SCHEDULE_DOW_MONDAY,
    SCHEDULE_DOW_TUESDAY,
    SCHEDULE_DOW_WEDNESDAY,
    SCHEDULE_DOW_THURSDAY,
    SCHEDULE_DOW_FRIDAY,
    SCHEDULE_DOW_SATURDAY,
]

SCHEDULE_MODE_HEAT: Final = 0x01

# ── Thermostat programming mode ────────────────────────────────────────
ATTR_THERMOSTAT_PROGRAMMING_MODE: Final = 0x0025
SCHEDULE_MODE_MANUAL: Final = 0  # Bit0=0, Bit1=0 -> manual, no preheat
SCHEDULE_MODE_SCHEDULE: Final = 1  # Bit0=1, Bit1=0 -> schedule, no preheat
SCHEDULE_MODE_SCHEDULE_PREHEAT: Final = 3  # Bit0=1, Bit1=1 -> schedule + preheat
SCHEDULE_MODE_ECO: Final = 4  # Bit2=1 -> eco / pause mode

# ── Programming mode option strings ───────────────────────────────────
PROGRAMMING_MODE_OPTION_MANUAL: Final = "manual"
PROGRAMMING_MODE_OPTION_SCHEDULE: Final = "schedule"
PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT: Final = "schedule_with_preheat"
PROGRAMMING_MODE_OPTION_PAUSE: Final = "pause"

PROGRAMMING_MODE_OPTIONS: Final = [
    PROGRAMMING_MODE_OPTION_MANUAL,
    PROGRAMMING_MODE_OPTION_SCHEDULE,
    PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT,
    PROGRAMMING_MODE_OPTION_PAUSE,
]

PROGRAMMING_MODE_TO_INT: Final[dict[str, int]] = {
    PROGRAMMING_MODE_OPTION_MANUAL: SCHEDULE_MODE_MANUAL,
    PROGRAMMING_MODE_OPTION_SCHEDULE: SCHEDULE_MODE_SCHEDULE,
    PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT: SCHEDULE_MODE_SCHEDULE_PREHEAT,
    PROGRAMMING_MODE_OPTION_PAUSE: SCHEDULE_MODE_ECO,
}

PROGRAMMING_MODE_FROM_INT: Final[dict[int, str]] = {
    v: k for k, v in PROGRAMMING_MODE_TO_INT.items()
}

# ── Setpoint command types ─────────────────────────────────────────────
SETPOINT_TYPE_USER: Final = 1  # Aggressive motor response (manual dial change)

# ── Setpoint change sources (from TRV reports) ────────────────────────
SETPOINT_SOURCE_MANUAL: Final = 0x00

# ── Supported TRV device filters ───────────────────────────────────────
SUPPORTED_TRV_DEVICES_Z2M: Final = [
    {"manufacturer": "Danfoss", "model": "Ally thermostat"},
    {"manufacturer": "Popp", "model": "Smart thermostat"},
    {"manufacturer": "Hive", "model": "Radiator valve"},
]

SUPPORTED_TRV_DEVICES_ZHA: Final = [
    {"manufacturer": "Danfoss", "model": "eTRV0100"},
    {"manufacturer": "Danfoss", "model": "eTRV0101"},
    {"manufacturer": "Danfoss", "model": "eTRV0103"},
    {"manufacturer": "D5X84YU", "model": "eT093WRO"},
    {"manufacturer": "D5X84YU", "model": "eT093WRG"},
    {"manufacturer": "Danfoss", "model": "TRV001"},
    {"manufacturer": "Danfoss", "model": "TRV003"},
]

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
PLATFORMS: Final = ["climate", "binary_sensor", "sensor", "select"]
