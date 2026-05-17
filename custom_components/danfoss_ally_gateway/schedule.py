"""Schedule data model for Danfoss Ally Gateway.

Data classes for representing weekly heating schedules:
- ScheduleEvent: A single time+temperature transition
- DaySchedule: Up to 6 events for one day
- WeeklySchedule: 7 days with validation and serialization
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .const import (
    SCHEDULE_MAX_DAILY_TRANSITIONS,
    SCHEDULE_MINUTES_PER_DAY,
)

_LOGGER = logging.getLogger(__name__)


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
        day_names = [
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]
        total_events = 0
        for i, day in enumerate(self.days):
            day_errors = day.validate()
            for err in day_errors:
                errors.append(f"{day_names[i]}: {err}")
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
