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
CONF_SCHEDULE_ENTITY: Final = "schedule_entity"
CONF_AT_HOME_TEMP: Final = "at_home_temperature"
CONF_AWAY_TEMP: Final = "away_temperature"
CONF_PREHEAT_ENABLED: Final = "preheat_enabled"

HEAT_SOURCE_CLIMATE: Final = "climate"
HEAT_SOURCE_BINARY_SENSOR: Final = "binary_sensor"

# ── Default schedule temperatures ──────────────────────────────────────
DEFAULT_AT_HOME_TEMP: Final = 21.0
DEFAULT_AWAY_TEMP: Final = 17.0

# ── Subentry types ─────────────────────────────────────────────────────
SUBENTRY_ROOM: Final = "room"

# ── Danfoss Zigbee cluster IDs ────────────────────────────────────────
CLUSTER_THERMOSTAT: Final = 0x0201
CLUSTER_TIME: Final = 0x000A

# ── Danfoss manufacturer-specific attribute IDs ───────────────────────
# Thermostat cluster (0x0201) manufacturer-specific attributes
ATTR_EXTERNAL_MEASURED_ROOM_SENSOR: Final = 0x4015  # Int16, temp × 100
ATTR_HEAT_AVAILABLE: Final = 0x4030  # Boolean
ATTR_LOAD_BALANCING_ENABLE: Final = 0x4032  # Boolean
ATTR_LOAD_ROOM_MEAN: Final = 0x4040  # Int16
ATTR_LOAD_ESTIMATE: Final = 0x404A  # Int16
ATTR_PREHEAT_STATUS: Final = 0x404F  # Boolean
ATTR_PREHEAT_TIME: Final = 0x4050  # UInt32 timestamp
ATTR_HEATING_SYSTEM_MODE: Final = 0x4059  # Enum8: 0=auto, 1=forced_boiler, 2=default
ATTR_WINDOW_OPEN_DETECTION: Final = 0x4000  # Enum8: states 0-4
ATTR_EXTERNAL_WINDOW_OPEN: Final = 0x4003  # Boolean
ATTR_RADIATOR_COVERED: Final = 0x4016  # Boolean
ATTR_SETPOINT_CHANGE_SOURCE: Final = 0x0030  # Enum8: 0=manual, 1=schedule, 2=external
ATTR_PI_HEATING_DEMAND: Final = 0x0008  # UInt8 (standard attribute)
ATTR_OCCUPIED_HEATING_SETPOINT: Final = 0x0012  # Int16, temp × 100 (standard)

# ── Danfoss manufacturer-specific command IDs ─────────────────────────
CMD_SETPOINT_COMMAND: Final = 0x40
CMD_PREHEAT_COMMAND: Final = 0x42

# ── Setpoint command types ─────────────────────────────────────────────
SETPOINT_TYPE_USER: Final = 1  # Aggressive motor response (manual dial change)
SETPOINT_TYPE_PREHEAT: Final = 2  # Internal use only

# ── Setpoint change source values ──────────────────────────────────────
SETPOINT_SOURCE_MANUAL: Final = 0x00
SETPOINT_SOURCE_SCHEDULE: Final = 0x01
SETPOINT_SOURCE_EXTERNAL: Final = 0x02

# ── Heating system mode values ─────────────────────────────────────────
HEATING_SYSTEM_MODE_AUTO: Final = 0
HEATING_SYSTEM_MODE_FORCED_BOILER: Final = 1
HEATING_SYSTEM_MODE_DEFAULT: Final = 2

# ── Availability ───────────────────────────────────────────────────────
TRV_AVAILABILITY_TIMEOUT: Final = 2 * 60 * 60  # 2 hours without update = unavailable

# ── Window open detection states ───────────────────────────────────────
WINDOW_OPEN_QUARANTINE: Final = 0
WINDOW_OPEN_CLOSED: Final = 1
WINDOW_OPEN_HOLD: Final = 2
WINDOW_OPEN_DETECTED: Final = 3
WINDOW_OPEN_EXTERNAL_OPEN: Final = 4

# ── Special values ─────────────────────────────────────────────────────
EXTERNAL_TEMP_DISABLED: Final = -8000  # Value to send to disable external temp

# ── Timing constants (seconds) ────────────────────────────────────────
# External temperature forwarding - exposed mode (radiator_covered=false)
EXT_TEMP_EXPOSED_MIN_INTERVAL: Final = 30 * 60  # 30 minutes
EXT_TEMP_EXPOSED_MAX_INTERVAL: Final = 3 * 60 * 60  # 3 hours
EXT_TEMP_EXPOSED_TIMEOUT: Final = 3 * 60 * 60  # 3 hours

# External temperature forwarding - covered mode (radiator_covered=true)
EXT_TEMP_COVERED_MIN_INTERVAL: Final = 5 * 60  # 5 minutes
EXT_TEMP_COVERED_MAX_INTERVAL: Final = 30 * 60  # 30 minutes
EXT_TEMP_COVERED_TIMEOUT: Final = 35 * 60  # 35 minutes

# Temperature change threshold for immediate send
EXT_TEMP_CHANGE_THRESHOLD: Final = 0.1  # Kelvin / degrees C

# Load balancing
LOAD_BALANCE_INTERVAL: Final = 15 * 60  # 15 minutes
LOAD_BALANCE_MAX_AGE: Final = 90 * 60  # 90 minutes
LOAD_BALANCE_INVALID_THRESHOLD: Final = -500
LOAD_BALANCE_DISABLED_VALUE: Final = -8000

# ── Danfoss manufacturer code ──────────────────────────────────────────
DANFOSS_MANUFACTURER_CODE: Final = 0x1246

# ── Remote climate sync ────────────────────────────────────────────────
REMOTE_CLIMATE_SUPPRESS_SECONDS: Final = 3.0  # Anti-echo suppression window

# ── Time sync ──────────────────────────────────────────────────────────
TIME_SYNC_INTERVAL: Final = 7 * 24 * 60 * 60  # Weekly

# ── Schedule management ───────────────────────────────────────────────
# ZCL Thermostat cluster commands for schedule
CMD_SET_WEEKLY_SCHEDULE: Final = 0x01
CMD_GET_WEEKLY_SCHEDULE: Final = 0x02
CMD_CLEAR_WEEKLY_SCHEDULE: Final = 0x03

# ── Schedule validation ────────────────────────────────────────────────
SCHEDULE_MAX_DAILY_TRANSITIONS: Final = 6
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

# Diagnostics: power-cycle detection
CLUSTER_DIAGNOSTICS: Final = 0x0B05
ATTR_SW_ERROR_CODE: Final = 0x4000
SW_ERROR_TIME_LOST: Final = (
    "invalid_clock_information"  # Error E10 (bit 9) — AU417130778872en-000102, §1.5
)

# Danfoss Ally system_status_code bitmap decoding (AU417130778872en-000102, §1.5).
# Keys are bit positions in the BITMAP16. Used by the ZHA backend to decode the
# raw numeric value to the same comma-separated string format that Z2M produces
# after zigbee-herdsman-converters PR #12026 + PR3.
DANFOSS_ALLY_SYSTEM_STATUS_CODES: Final[dict[int, str]] = {
    0: "top_pcb_sensor_error",
    1: "side_pcb_sensor_error",
    2: "non_volatile_memory_error",
    3: "unknown_hw_error",
    5: "motor_error",
    7: "invalid_internal_communication",
    9: "invalid_clock_information",
    11: "radio_communication_error",
    12: "encoder_jammed",
    13: "low_battery",
    14: "critical_low_battery",
}

# Power cycle check interval (how often to poll for time-lost)
POWER_CYCLE_CHECK_INTERVAL: Final = 6 * 60 * 60  # 6 hours (safety-net fallback)

# ── Thermostat programming mode ────────────────────────────────────────
# Thermostat Programming Operation Mode (0x0025)
ATTR_THERMOSTAT_PROGRAMMING_MODE: Final = 0x0025
SCHEDULE_MODE_MANUAL: Final = 0  # Bit0=0, Bit1=0 -> manual, no preheat
SCHEDULE_MODE_SCHEDULE: Final = 1  # Bit0=1, Bit1=0 -> schedule, no preheat
SCHEDULE_MODE_SCHEDULE_PREHEAT: Final = 3  # Bit0=1, Bit1=1 -> schedule + preheat
SCHEDULE_MODE_ECO: Final = 4  # Bit2=1 — unused: not implemented by Danfoss firmware

# ── Programming mode option strings ───────────────────────────────────
PROGRAMMING_MODE_OPTION_MANUAL: Final = "manual"
PROGRAMMING_MODE_OPTION_SCHEDULE: Final = "schedule"
PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT: Final = "schedule_with_preheat"
PROGRAMMING_MODE_OPTION_PAUSE: Final = "pause"  # Unused: kept for reference only

PROGRAMMING_MODE_OPTIONS: Final = [
    PROGRAMMING_MODE_OPTION_MANUAL,
    PROGRAMMING_MODE_OPTION_SCHEDULE,
    PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT,
]

PROGRAMMING_MODE_TO_INT: Final[dict[str, int]] = {
    PROGRAMMING_MODE_OPTION_MANUAL: SCHEDULE_MODE_MANUAL,
    PROGRAMMING_MODE_OPTION_SCHEDULE: SCHEDULE_MODE_SCHEDULE,
    PROGRAMMING_MODE_OPTION_SCHEDULE_PREHEAT: SCHEDULE_MODE_SCHEDULE_PREHEAT,
}

PROGRAMMING_MODE_FROM_INT: Final[dict[int, str]] = {
    v: k for k, v in PROGRAMMING_MODE_TO_INT.items()
}

# ── Supported TRV device filters ───────────────────────────────────────
SUPPORTED_TRV_DEVICES_Z2M: Final = [
    {"manufacturer": "Danfoss", "model": "Ally thermostat"},
    {"manufacturer": "Popp", "model": "Smart thermostat"},
    {"manufacturer": "Hive", "model": "Radiator valve"},
]

SUPPORTED_TRV_DEVICES_ZHA: Final = [
    # Danfoss Ally variants
    {"manufacturer": "Danfoss", "model": "eTRV0100"},
    {"manufacturer": "Danfoss", "model": "eTRV0101"},
    {"manufacturer": "Danfoss", "model": "eTRV0103"},
    # Popp TRVs (raw Zigbee manufacturer string)
    {"manufacturer": "D5X84YU", "model": "eT093WRO"},
    {"manufacturer": "D5X84YU", "model": "eT093WRG"},
    # Hive TRVs (report as Danfoss manufacturer)
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
Z2M_ATTR_HEATING_SYSTEM_MODE: Final = "heating_system_mode"
Z2M_ATTR_WINDOW_OPEN_DETECTION: Final = "window_open_internal"
Z2M_ATTR_EXTERNAL_WINDOW_OPEN: Final = "window_open_external"
Z2M_ATTR_RADIATOR_COVERED: Final = "radiator_covered"
Z2M_ATTR_SETPOINT_CHANGE_SOURCE: Final = "setpoint_change_source"
Z2M_ATTR_PI_HEATING_DEMAND: Final = "pi_heating_demand"
Z2M_ATTR_OCCUPIED_HEATING_SETPOINT: Final = "occupied_heating_setpoint"
Z2M_ATTR_LOCAL_TEMPERATURE: Final = "local_temperature"
Z2M_ATTR_PROGRAMMING_MODE: Final = "programming_operation_mode"

# ── Platforms ──────────────────────────────────────────────────────────
PLATFORMS: Final = ["climate", "binary_sensor", "sensor", "select", "switch"]
