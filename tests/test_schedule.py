"""Tests for the schedule data model."""

from __future__ import annotations

import pytest

from custom_components.danfoss_ally_gateway.schedule import (
    DaySchedule,
    ScheduleEvent,
    WeeklySchedule,
)

# ── ScheduleEvent ─────────────────────────────────────────────────────


class TestScheduleEvent:
    """Tests for ScheduleEvent dataclass."""

    def test_basic_properties(self):
        ev = ScheduleEvent(minutes_since_midnight=480, temperature=21.0)
        assert ev.hours == 8
        assert ev.mins == 0
        assert ev.time_str == "08:00"
        assert ev.setpoint_x100 == 2100

    def test_midnight(self):
        ev = ScheduleEvent(minutes_since_midnight=0, temperature=18.0)
        assert ev.hours == 0
        assert ev.mins == 0
        assert ev.time_str == "00:00"

    def test_end_of_day(self):
        ev = ScheduleEvent(minutes_since_midnight=1439, temperature=16.5)
        assert ev.hours == 23
        assert ev.mins == 59
        assert ev.time_str == "23:59"
        assert ev.setpoint_x100 == 1650

    def test_ordering(self):
        """Events should sort by minutes_since_midnight, then temperature."""
        ev1 = ScheduleEvent(minutes_since_midnight=360, temperature=21.0)
        ev2 = ScheduleEvent(minutes_since_midnight=480, temperature=18.0)
        ev3 = ScheduleEvent(minutes_since_midnight=360, temperature=18.0)
        assert ev1 < ev2
        assert ev3 < ev1  # Same time, lower temp comes first

    def test_frozen(self):
        ev = ScheduleEvent(minutes_since_midnight=360, temperature=21.0)
        with pytest.raises(AttributeError):
            ev.temperature = 22.0  # type: ignore[misc]

    def test_repr(self):
        ev = ScheduleEvent(minutes_since_midnight=480, temperature=21.5)
        assert "08:00" in repr(ev)
        assert "21.5" in repr(ev)

    def test_half_degree_setpoint(self):
        ev = ScheduleEvent(minutes_since_midnight=0, temperature=20.5)
        assert ev.setpoint_x100 == 2050


# ── DaySchedule ───────────────────────────────────────────────────────


class TestDaySchedule:
    """Tests for DaySchedule."""

    def test_empty(self):
        day = DaySchedule()
        assert day.is_empty
        assert day.last_temperature is None
        assert day.first_temperature is None
        assert day.validate() == []

    def test_with_events(self):
        events = [
            ScheduleEvent(360, 21.0),
            ScheduleEvent(480, 18.0),
            ScheduleEvent(1320, 16.0),
        ]
        day = DaySchedule(events=events)
        assert not day.is_empty
        assert day.first_temperature == 21.0
        assert day.last_temperature == 16.0
        assert day.validate() == []

    def test_validation_too_many_events(self):
        events = [ScheduleEvent(i * 100, 21.0) for i in range(7)]
        day = DaySchedule(events=events)
        errors = day.validate()
        assert len(errors) == 1
        assert "Too many events" in errors[0]

    def test_validation_invalid_time(self):
        events = [ScheduleEvent(-1, 21.0)]
        day = DaySchedule(events=events)
        errors = day.validate()
        assert any("invalid time" in e for e in errors)

    def test_validation_time_too_large(self):
        events = [ScheduleEvent(1440, 21.0)]
        day = DaySchedule(events=events)
        errors = day.validate()
        assert any("invalid time" in e for e in errors)

    def test_validation_not_chronological(self):
        events = [
            ScheduleEvent(480, 21.0),
            ScheduleEvent(360, 18.0),
        ]
        day = DaySchedule(events=events)
        errors = day.validate()
        assert any("not chronological" in e.lower() for e in errors)

    def test_validation_duplicate_times(self):
        events = [
            ScheduleEvent(480, 21.0),
            ScheduleEvent(480, 18.0),
        ]
        day = DaySchedule(events=events)
        errors = day.validate()
        assert len(errors) >= 1

    def test_sorted(self):
        events = [
            ScheduleEvent(720, 18.0),
            ScheduleEvent(360, 21.0),
        ]
        day = DaySchedule(events=events)
        sorted_day = day.sorted()
        assert sorted_day.events[0].minutes_since_midnight == 360
        assert sorted_day.events[1].minutes_since_midnight == 720

    def test_max_events_valid(self):
        """Exactly 6 events should be valid."""
        events = [ScheduleEvent(i * 200, 20.0) for i in range(6)]
        day = DaySchedule(events=events)
        assert day.validate() == []


# ── WeeklySchedule ────────────────────────────────────────────────────


class TestWeeklySchedule:
    """Tests for WeeklySchedule."""

    def test_empty_schedule(self):
        ws = WeeklySchedule()
        assert ws.is_empty
        assert ws.total_events == 0
        assert ws.validate() == []

    def test_schedule_with_days(self):
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[  # Monday
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        assert not ws.is_empty
        assert ws.total_events == 2

    def test_validate_total_events_exceeded(self):
        ws = WeeklySchedule()
        # 7 days x 6 events = 42 (ok), but adding one more exceeds
        for i in range(7):
            ws.days[i] = DaySchedule(
                events=[ScheduleEvent(j * 200, 20.0) for j in range(6)]
            )
        assert ws.total_events == 42
        assert ws.validate() == []

        # Add one more event to push over
        ws.days[0].events.append(ScheduleEvent(1300, 19.0))
        errors = ws.validate()
        assert any("exceeds capacity" in e.lower() for e in errors)

    def test_to_dict_from_dict_roundtrip(self):
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        ws.days[5] = DaySchedule(
            events=[
                ScheduleEvent(480, 22.5),
            ]
        )

        data = ws.to_dict()
        restored = WeeklySchedule.from_dict(data)

        assert len(restored.days[1].events) == 2
        assert restored.days[1].events[0].minutes_since_midnight == 360
        assert restored.days[1].events[0].temperature == 21.0
        assert restored.days[1].events[1].minutes_since_midnight == 1320
        assert restored.days[1].events[1].temperature == 18.0
        assert len(restored.days[5].events) == 1
        assert restored.days[5].events[0].temperature == 22.5

    def test_from_dict_empty(self):
        ws = WeeklySchedule.from_dict({})
        assert ws.is_empty

    def test_from_dict_partial(self):
        data = {"days": [[{"time": 480, "temp": 21.0}]]}
        ws = WeeklySchedule.from_dict(data)
        assert len(ws.days[0].events) == 1
        # Other days should be empty
        for i in range(1, 7):
            assert ws.days[i].is_empty

    def test_seven_days_initialized(self):
        ws = WeeklySchedule()
        assert len(ws.days) == 7
        for day in ws.days:
            assert isinstance(day, DaySchedule)
