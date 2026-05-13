"""Backend abstraction for Danfoss Ally TRV communication."""

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant


@dataclass
class TRVState:
    """Snapshot of a single TRV's current state"""

    entity_id: str
    local_temperature: float | None = None
    occupied_heating_setpoint: float | None = None
    pi_heating_demand: int | None = None
    load_estimate: int | None = None
    load_balancing_enable: bool | None = None
    heat_available: bool | None = None
    preheat_status: bool | None = None
    preheat_time: int | None = None
    window_open_detection: int | None = None
    external_window_open: bool | None = None
    setpoint_change_source: int | None = None
    radiator_covered: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# Type alias for TRV state change callbacks
TRVStateCallback = Callable[[str, TRVState], None]

# Type alias for device announce callbacks (receives trv_id)
DeviceAnnounceCallback = Callable[[str], None]


class DanfossBackend(abc.ABC):
    """Abstract base class for Danfoss Ally TRV communication backends.

    Each backend implementation (Z2M, ZHA) must provide methods to:
    - Subscribe to TRV state updates
    - Write attributes to TRVs
    - Send commands to TRVs
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the backend."""
        self.hass = hass
        self._state_callbacks: list[TRVStateCallback] = []
        self._announce_callbacks: list[DeviceAnnounceCallback] = []

    def register_state_callback(self, callback: TRVStateCallback) -> Callable[[], None]:
        """Register a callback for TRV state changes.

        Returns a callable that unregisters the callback.
        """
        self._state_callbacks.append(callback)

        def _unregister() -> None:
            self._state_callbacks.remove(callback)

        return _unregister

    def _fire_state_update(self, trv_id: str, state: TRVState) -> None:
        """Notify all registered callbacks of a TRV state change."""
        for callback in self._state_callbacks:
            callback(trv_id, state)

    def register_announce_callback(
        self, callback: DeviceAnnounceCallback
    ) -> Callable[[], None]:
        """Register a callback for device announce (rejoin) events.

        The callback receives the trv_id when a subscribed TRV sends a
        Zigbee Device Announce (e.g. after battery change / power cycle).
        Returns a callable that unregisters the callback.
        """
        self._announce_callbacks.append(callback)

        def _unregister() -> None:
            self._announce_callbacks.remove(callback)

        return _unregister

    def _fire_device_announce(self, trv_id: str) -> None:
        """Notify all registered callbacks of a device announce event."""
        for callback in self._announce_callbacks:
            callback(trv_id)

    @abc.abstractmethod
    async def async_setup(self) -> None:
        """Set up the backend (connect, subscribe, etc.)."""

    @abc.abstractmethod
    async def async_teardown(self) -> None:
        """Tear down the backend (disconnect, unsubscribe, etc.)."""

    @abc.abstractmethod
    async def async_subscribe_trv(self, trv_id: str) -> None:
        """Start listening for state updates from a specific TRV."""

    @abc.abstractmethod
    async def async_unsubscribe_trv(self, trv_id: str) -> None:
        """Stop listening for state updates from a specific TRV."""

    @abc.abstractmethod
    async def async_get_trv_state(self, trv_id: str) -> TRVState | None:
        """Get the current state of a TRV (best-effort, may be cached)."""

    # ── Attribute writes ───────────────────────────────────────────────

    @abc.abstractmethod
    async def async_set_external_temperature(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write external measured room sensor value to TRV.

        Args:
            trv_id: The TRV identifier.
            temperature: Temperature in degrees Celsius.
                         Send EXTERNAL_TEMP_DISABLED (-80.0) to disable.
        """

    @abc.abstractmethod
    async def async_set_occupied_heating_setpoint(
        self, trv_id: str, temperature: float
    ) -> None:
        """Write OccupiedHeatingSetpoint to TRV (Type 0 / gentle)."""

    @abc.abstractmethod
    async def async_set_heat_available(self, trv_id: str, available: bool) -> None:
        """Write HeatAvailable attribute to TRV."""

    @abc.abstractmethod
    async def async_set_load_room_mean(self, trv_id: str, value: int) -> None:
        """Write LoadRoomMean to TRV."""

    @abc.abstractmethod
    async def async_set_external_window_open(self, trv_id: str, is_open: bool) -> None:
        """Write ExternalOpenWindowDetected to TRV."""

    # ── Commands ───────────────────────────────────────────────────────

    @abc.abstractmethod
    async def async_send_setpoint_command(
        self, trv_id: str, temperature: float, command_type: int
    ) -> None:
        """Send SetpointCommand (0x40) to TRV.

        Args:
            trv_id: The TRV identifier.
            temperature: Target temperature in degrees Celsius.
            command_type: 0=schedule/gentle, 1=user/aggressive.
        """

    @abc.abstractmethod
    async def async_send_preheat_command(self, trv_id: str, timestamp: int) -> None:
        """Send PreHeatCommand (0x42) to force TRV to preheat to a timestamp.

        Per Danfoss spec (AU417130778872en-000102, §3.2):
        Command 0x42 on cluster 0x0201 (hvacThermostat) with parameters:
          - enum8 = 0x00 (force preheat)
          - uint32 = timestamp from source TRV's preheat_time attribute
        """

    @abc.abstractmethod
    async def async_sync_time(self, trv_id: str) -> None:
        """Synchronize time cluster attributes to TRV."""

    # ── Schedule ───────────────────────────────────────────────────────

    @abc.abstractmethod
    async def async_set_weekly_schedule(
        self,
        trv_id: str,
        day_of_week: int,
        num_transitions: int,
        mode: int,
        transitions: list[tuple[int, int]],
    ) -> None:
        """Send SetWeeklySchedule command for a single day.

        Args:
            trv_id: The TRV identifier.
            day_of_week: ZCL day-of-week bitmask (single day).
            num_transitions: Number of transitions.
            mode: ZCL mode field (0x01 = heat).
            transitions: List of (minutes_since_midnight, setpoint_x100).
        """

    @abc.abstractmethod
    async def async_get_weekly_schedule(
        self, trv_id: str, day_of_week: int
    ) -> list[tuple[int, int]] | None:
        """Send GetWeeklySchedule and return transitions for the requested day.

        Args:
            trv_id: The TRV identifier.
            day_of_week: ZCL day-of-week bitmask (single day).

        Returns:
            List of (minutes_since_midnight, setpoint_x100) or None on error.
        """

    @abc.abstractmethod
    async def async_clear_weekly_schedule(self, trv_id: str) -> None:
        """Send ClearWeeklySchedule command to delete all schedule events."""

    @abc.abstractmethod
    async def async_set_programming_mode(self, trv_id: str, mode: int) -> None:
        """Write Thermostat Programming Operation Mode (0x0025).

        Args:
            trv_id: The TRV identifier.
            mode: 0=manual, 1=schedule, 3=schedule+preheat.
        """

    @abc.abstractmethod
    async def async_read_sw_error_code(self, trv_id: str) -> str | None:
        """Read SW Error Code from Diagnostics cluster (0x0B05, 0x4000).

        Returns a comma-separated string of active error names (e.g.
        ``"invalid_clock_information,low_battery"``), ``"ok"`` when no errors
        are active, or ``None`` if the value is unavailable.
        """
