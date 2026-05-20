"""Schedule management for Danfoss Ally Gateway.

Implements full schedule programming via ZCL SetWeeklySchedule:
- Data model for weekly schedules (7 days x up to 6 transitions each)
- Midnight crossing logic per Danfoss spec
- Serialization to/from ZCL SetWeeklySchedule payload format
- Schedule comparison for read-back verification
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

from .const import (
    SCHEDULE_DOW_ALL,
    SCHEDULE_MAX_DAILY_TRANSITIONS,
    SCHEDULE_MINUTES_PER_DAY,
    SCHEDULE_MODE_HEAT,
)

_LOGGER = logging.getLogger(__name__)

DAY_NAMES: Final = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]


@dataclass(frozen=True, order=True)
class ScheduleEvent:
    """A single schedule event (transition) within a day.

    Attributes:
        minutes_since_midnight: Time of the event (0-1439).
        temperature: Target temperature in degrees Celsius (e.g., 21.0).
    """

    minutes_since_midnight: int
    temperature: float

    @property
    def hours(self) -> int:
        """Return the hour component."""
        return self.minutes_since_midnight // 60

    @property
    def mins(self) -> int:
        """Return the minute component."""
        return self.minutes_since_midnight % 60

    @property
    def time_str(self) -> str:
        """Return human-readable time string (HH:MM)."""
        return f"{self.hours:02d}:{self.mins:02d}"

    @property
    def setpoint_x100(self) -> int:
        """Return the setpoint as ZCL Int16 (temp x 100)."""
        return int(self.temperature * 100)

    def __repr__(self) -> str:
        return f"{self.time_str} -> {self.temperature}C"


@dataclass
class DaySchedule:
    """Schedule for a single day of the week.

    Contains up to SCHEDULE_MAX_DAILY_TRANSITIONS (6) events,
    ordered chronologically by minutes_since_midnight.
    """

    events: list[ScheduleEvent] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate the day schedule and return a list of errors."""
        errors: list[str] = []
        if len(self.events) > SCHEDULE_MAX_DAILY_TRANSITIONS:
            errors.append(
                f"Too many events: {len(self.events)} "
                f"(max {SCHEDULE_MAX_DAILY_TRANSITIONS})"
            )
        for i, ev in enumerate(self.events):
            if (
                ev.minutes_since_midnight < 0
                or ev.minutes_since_midnight >= SCHEDULE_MINUTES_PER_DAY
            ):
                errors.append(
                    f"Event {i}: invalid time {ev.minutes_since_midnight} "
                    f"(must be 0-{SCHEDULE_MINUTES_PER_DAY - 1})"
                )
        # Check chronological order
        for i in range(1, len(self.events)):
            if (
                self.events[i].minutes_since_midnight
                <= self.events[i - 1].minutes_since_midnight
            ):
                errors.append(
                    f"Events not chronological: {self.events[i - 1]} >= {self.events[i]}"
                )
        return errors

    @property
    def is_empty(self) -> bool:
        """Return True if no events are configured."""
        return len(self.events) == 0

    @property
    def last_temperature(self) -> float | None:
        """Return the temperature of the last event of the day (carry-over)."""
        if not self.events:
            return None
        return self.events[-1].temperature

    @property
    def first_temperature(self) -> float | None:
        """Return the temperature of the first event of the day."""
        if not self.events:
            return None
        return self.events[0].temperature

    def sorted(self) -> DaySchedule:
        """Return a copy with events sorted chronologically."""
        return DaySchedule(events=sorted(self.events))


@dataclass
class WeeklySchedule:
    """A complete weekly schedule for a room / TRV.

    Days are indexed 0=Sunday through 6=Saturday, matching ZCL day-of-week
    bitmask order.
    """

    days: list[DaySchedule] = field(
        default_factory=lambda: [DaySchedule() for _ in range(7)]
    )

    def validate(self) -> list[str]:
        """Validate the entire weekly schedule."""
        errors: list[str] = []
        total_events = 0
        for i, day in enumerate(self.days):
            day_errors = day.validate()
            for err in day_errors:
                errors.append(f"{DAY_NAMES[i]}: {err}")
            total_events += len(day.events)

        if total_events > 42:
            errors.append(f"Total events ({total_events}) exceeds capacity (42)")
        return errors

    @property
    def total_events(self) -> int:
        """Return total number of events across all days."""
        return sum(len(d.events) for d in self.days)

    @property
    def is_empty(self) -> bool:
        """Return True if no day has any events."""
        return all(d.is_empty for d in self.days)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for storage."""
        return {
            "days": [
                [
                    {"time": ev.minutes_since_midnight, "temp": ev.temperature}
                    for ev in day.events
                ]
                for day in self.days
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeeklySchedule:
        """Deserialize from a JSON-compatible dict."""
        schedule = cls()
        days_data = data.get("days", [])
        for i, day_data in enumerate(days_data):
            if i >= 7:
                break
            events = []
            for ev_data in day_data:
                events.append(
                    ScheduleEvent(
                        minutes_since_midnight=ev_data["time"],
                        temperature=ev_data["temp"],
                    )
                )
            schedule.days[i] = DaySchedule(events=events)
        return schedule


def apply_midnight_crossing(schedule: WeeklySchedule) -> WeeklySchedule:
    """Apply Danfoss midnight crossing logic to a weekly schedule.

    Per Danfoss spec:
    - If a day's last event carries a temperature into the next day, and
      the next day's first event is NOT at 00:00, we need to add a 23:59
      event at the end of the current day (with the current day's end temp)
      and a 00:00 event at the start of the next day (with the same temp
      if it differs from the next day's first event).
    - If the next day starts at 00:00 with the same temperature as the
      carry-over, the 23:59 event on the previous day is unnecessary.
    - If the next day starts at 00:00, no 23:59 is needed on the
      previous day for that transition.

    This ensures the TRV always knows what temperature to use at midnight.
    """
    result = WeeklySchedule()
    for i in range(7):
        result.days[i] = DaySchedule(events=list(schedule.days[i].events))

    for day_idx in range(7):
        current_day = result.days[day_idx]
        next_day_idx = (day_idx + 1) % 7
        next_day = result.days[next_day_idx]

        if current_day.is_empty:
            continue

        carry_temp = current_day.last_temperature

        if next_day.is_empty:
            # No events tomorrow - no crossing needed
            continue

        next_first = next_day.events[0]

        if next_first.minutes_since_midnight == 0:
            # Next day starts at 00:00 — remove any 23:59 event we might
            # have on the current day (it's redundant)
            current_day.events = [
                ev
                for ev in current_day.events
                if ev.minutes_since_midnight != SCHEDULE_MINUTES_PER_DAY - 1
            ]
        else:
            # Next day does NOT start at 00:00 — we need to bridge midnight.
            # Add 23:59 on current day with carry_temp if not already present.
            has_2359 = any(
                ev.minutes_since_midnight == SCHEDULE_MINUTES_PER_DAY - 1
                for ev in current_day.events
            )
            if not has_2359 and carry_temp is not None:
                # Only add if we have room
                if len(current_day.events) < SCHEDULE_MAX_DAILY_TRANSITIONS:
                    current_day.events.append(
                        ScheduleEvent(
                            minutes_since_midnight=SCHEDULE_MINUTES_PER_DAY - 1,
                            temperature=carry_temp,
                        )
                    )
                    current_day.events.sort()
                else:
                    _LOGGER.warning(
                        "Day %d: cannot add 23:59 event (already at max transitions)",
                        day_idx,
                    )

            # Add 00:00 on next day with carry_temp if not already present
            has_0000 = any(ev.minutes_since_midnight == 0 for ev in next_day.events)
            if not has_0000 and carry_temp is not None:
                if len(next_day.events) < SCHEDULE_MAX_DAILY_TRANSITIONS:
                    next_day.events.insert(
                        0,
                        ScheduleEvent(
                            minutes_since_midnight=0,
                            temperature=carry_temp,
                        ),
                    )
                else:
                    _LOGGER.warning(
                        "Day %d: cannot add 00:00 event (already at max transitions)",
                        next_day_idx,
                    )

    return result


def build_zcl_set_weekly_payloads(
    schedule: WeeklySchedule,
) -> list[dict[str, Any]]:
    """Convert a WeeklySchedule into ZCL SetWeeklySchedule command payloads.

    Returns a list of dicts, one per day that has events. Each dict contains:
    - num_transitions: number of events for this day
    - day_of_week: ZCL day-of-week bitmask (single day)
    - mode: ZCL mode (heating = 0x01)
    - transitions: list of (minutes_since_midnight, setpoint_x100) tuples

    Per ZCL spec, SetWeeklySchedule is sent once per day (or can combine
    multiple days with the same schedule). We send one command per day for
    simplicity and clarity.
    """
    payloads: list[dict[str, Any]] = []

    for day_idx in range(7):
        day = schedule.days[day_idx]
        if day.is_empty:
            continue

        transitions = [
            (ev.minutes_since_midnight, ev.setpoint_x100) for ev in day.events
        ]

        payloads.append(
            {
                "num_transitions": len(transitions),
                "day_of_week": SCHEDULE_DOW_ALL[day_idx],
                "mode": SCHEDULE_MODE_HEAT,
                "transitions": transitions,
            }
        )

    return payloads


def parse_zcl_get_weekly_response(
    day_of_week_mask: int,
    mode: int,
    transitions: list[tuple[int, int]],
) -> dict[int, DaySchedule]:
    """Parse a ZCL GetWeeklySchedule response into DaySchedule objects.

    Args:
        day_of_week_mask: Bitmask of days this response covers.
        mode: ZCL mode field.
        transitions: List of (minutes_since_midnight, setpoint_x100) tuples.

    Returns:
        Dict mapping day index (0-6) to DaySchedule.
    """
    result: dict[int, DaySchedule] = {}

    for day_idx in range(7):
        if day_of_week_mask & SCHEDULE_DOW_ALL[day_idx]:
            events = [
                ScheduleEvent(
                    minutes_since_midnight=t[0],
                    temperature=t[1] / 100.0,
                )
                for t in transitions
            ]
            result[day_idx] = DaySchedule(events=events)

    return result


def schedules_match(
    expected: WeeklySchedule,
    actual: WeeklySchedule,
    tolerance: float = 0.01,
) -> bool:
    """Compare two weekly schedules for equivalence.

    Used for read-back verification. Checks that each day has the same
    events with temperatures within the specified tolerance.
    """
    for day_idx in range(7):
        expected_day = expected.days[day_idx]
        actual_day = actual.days[day_idx]

        if len(expected_day.events) != len(actual_day.events):
            _LOGGER.debug(
                "Day %d: expected %d events, got %d",
                day_idx,
                len(expected_day.events),
                len(actual_day.events),
            )
            return False

        for i, (exp_ev, act_ev) in enumerate(
            zip(expected_day.events, actual_day.events, strict=False)
        ):
            if exp_ev.minutes_since_midnight != act_ev.minutes_since_midnight:
                _LOGGER.debug(
                    "Day %d event %d: expected time %d, got %d",
                    day_idx,
                    i,
                    exp_ev.minutes_since_midnight,
                    act_ev.minutes_since_midnight,
                )
                return False
            if abs(exp_ev.temperature - act_ev.temperature) > tolerance:
                _LOGGER.debug(
                    "Day %d event %d: expected temp %.2f, got %.2f",
                    day_idx,
                    i,
                    exp_ev.temperature,
                    act_ev.temperature,
                )
                return False

    return True


# ── HA schedule entity day name mapping ────────────────────────────────
# HA schedule helper uses lowercase English day names; ZCL uses 0=Sunday..6=Saturday.
_HA_DAY_TO_INDEX: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

# Minimum at-home block duration in minutes
MIN_BLOCK_DURATION: int = 30

# Maximum at-home blocks per day (= 3 → 6 transitions)
MAX_BLOCKS_PER_DAY: int = 3


def _time_str_to_minutes(time_str: str) -> int:
    """Convert 'HH:MM:SS' or 'HH:MM' to minutes since midnight."""
    parts = time_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def from_ha_schedule(
    schedule_data: list[dict[str, Any]],
    at_home_temp: float,
    away_temp: float,
) -> WeeklySchedule:
    """Convert HA schedule helper data to a WeeklySchedule.

    Args:
        schedule_data: List of schedule block dicts from the HA schedule
            entity's schedule attribute. Each block has keys:
            - "from": start time "HH:MM:SS"
            - "to": end time "HH:MM:SS"
            - "days": list of day abbreviations (e.g. ["mon", "tue"])
        at_home_temp: Temperature for "at home" (on) periods.
        away_temp: Temperature for "away" (off) periods.

    Returns:
        A WeeklySchedule with transitions derived from the on/off blocks.

    Raises:
        ValueError: If blocks exceed max per day or are too short.
    """
    # Collect blocks per day
    day_blocks: dict[int, list[tuple[int, int]]] = {i: [] for i in range(7)}

    for block in schedule_data:
        from_min = _time_str_to_minutes(block["from"])
        to_min = _time_str_to_minutes(block["to"])
        days = block.get("days", [])

        for day_abbr in days:
            day_idx = _HA_DAY_TO_INDEX.get(day_abbr.lower())
            if day_idx is not None:
                day_blocks[day_idx].append((from_min, to_min))

    schedule = WeeklySchedule()

    for day_idx in range(7):
        blocks = sorted(day_blocks[day_idx])

        if not blocks:
            continue

        if len(blocks) > MAX_BLOCKS_PER_DAY:
            raise ValueError(
                f"{DAY_NAMES[day_idx]}: {len(blocks)} at-home blocks "
                f"exceeds maximum of {MAX_BLOCKS_PER_DAY}"
            )

        events: list[ScheduleEvent] = []
        for from_min, to_min in blocks:
            duration = to_min - from_min
            if duration < MIN_BLOCK_DURATION:
                raise ValueError(
                    f"{DAY_NAMES[day_idx]}: block {from_min}-{to_min} "
                    f"is {duration} minutes (minimum {MIN_BLOCK_DURATION})"
                )
            events.append(
                ScheduleEvent(
                    minutes_since_midnight=from_min,
                    temperature=at_home_temp,
                )
            )
            events.append(
                ScheduleEvent(
                    minutes_since_midnight=to_min,
                    temperature=away_temp,
                )
            )

        schedule.days[day_idx] = DaySchedule(events=sorted(events))

    return schedule
