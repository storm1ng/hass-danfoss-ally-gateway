"""Tests for the schedule data model."""

from __future__ import annotations

import pytest

from custom_components.danfoss_ally_gateway.const import (
    SCHEDULE_DOW_MONDAY,
    SCHEDULE_DOW_SATURDAY,
    SCHEDULE_DOW_SUNDAY,
    SCHEDULE_DOW_TUESDAY,
    SCHEDULE_MODE_HEAT,
)
from custom_components.danfoss_ally_gateway.schedule import (
    DaySchedule,
    ScheduleEvent,
    WeeklySchedule,
    apply_midnight_crossing,
    build_zcl_set_weekly_payloads,
    from_ha_schedule,
    parse_zcl_get_weekly_response,
    schedules_match,
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


# ── Midnight Crossing ─────────────────────────────────────────────────


class TestMidnightCrossing:
    """Tests for apply_midnight_crossing()."""

    def test_no_crossing_needed_next_day_starts_at_0000(self):
        """If next day starts at 00:00, no 23:59 event needed."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[  # Monday
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        ws.days[2] = DaySchedule(
            events=[  # Tuesday starts at 00:00
                ScheduleEvent(0, 17.0),
                ScheduleEvent(360, 21.0),
            ]
        )

        result = apply_midnight_crossing(ws)
        # Monday should NOT have 23:59 added
        assert all(ev.minutes_since_midnight != 1439 for ev in result.days[1].events)

    def test_crossing_adds_2359_and_0000(self):
        """If next day does NOT start at 00:00, bridge midnight."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[  # Monday
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        ws.days[2] = DaySchedule(
            events=[  # Tuesday starts at 06:00
                ScheduleEvent(360, 21.0),
            ]
        )

        result = apply_midnight_crossing(ws)

        # Monday should have 23:59 with 18.0 (carry-over temp)
        monday_times = [ev.minutes_since_midnight for ev in result.days[1].events]
        assert 1439 in monday_times
        ev_2359 = [
            ev for ev in result.days[1].events if ev.minutes_since_midnight == 1439
        ][0]
        assert ev_2359.temperature == 18.0

        # Tuesday should have 00:00 with 18.0 (carry-over from Monday)
        tuesday_times = [ev.minutes_since_midnight for ev in result.days[2].events]
        assert 0 in tuesday_times
        ev_0000 = [
            ev for ev in result.days[2].events if ev.minutes_since_midnight == 0
        ][0]
        assert ev_0000.temperature == 18.0

    def test_no_crossing_empty_day(self):
        """Empty days should not cause crossing."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[  # Monday only
                ScheduleEvent(360, 21.0),
            ]
        )
        # Tuesday is empty
        result = apply_midnight_crossing(ws)
        # Monday should not get 23:59 (next day is empty)
        assert all(ev.minutes_since_midnight != 1439 for ev in result.days[1].events)

    def test_crossing_removes_redundant_2359(self):
        """If next day starts at 00:00, remove existing 23:59 on current day."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1439, 18.0),  # Pre-existing 23:59
            ]
        )
        ws.days[2] = DaySchedule(
            events=[
                ScheduleEvent(0, 17.0),  # Starts at 00:00
                ScheduleEvent(360, 21.0),
            ]
        )

        result = apply_midnight_crossing(ws)
        # The 23:59 on Monday should be removed
        assert all(ev.minutes_since_midnight != 1439 for ev in result.days[1].events)

    def test_wraps_around_saturday_to_sunday(self):
        """Midnight crossing should work between Saturday (6) and Sunday (0)."""
        ws = WeeklySchedule()
        ws.days[6] = DaySchedule(
            events=[  # Saturday
                ScheduleEvent(360, 22.0),
                ScheduleEvent(1320, 19.0),
            ]
        )
        ws.days[0] = DaySchedule(
            events=[  # Sunday starts at 08:00
                ScheduleEvent(480, 20.0),
            ]
        )

        result = apply_midnight_crossing(ws)

        # Saturday should have 23:59 with 19.0
        sat_times = [ev.minutes_since_midnight for ev in result.days[6].events]
        assert 1439 in sat_times

        # Sunday should have 00:00 with 19.0
        sun_times = [ev.minutes_since_midnight for ev in result.days[0].events]
        assert 0 in sun_times

    def test_does_not_duplicate_existing_0000(self):
        """If next day already has 00:00, don't add another."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        ws.days[2] = DaySchedule(
            events=[
                ScheduleEvent(0, 18.0),  # Already has 00:00
                ScheduleEvent(360, 21.0),
            ]
        )

        result = apply_midnight_crossing(ws)
        # Tuesday should still have exactly one 00:00
        count_0000 = sum(
            1 for ev in result.days[2].events if ev.minutes_since_midnight == 0
        )
        assert count_0000 == 1

    def test_max_transitions_prevents_2359_addition(self):
        """If current day already has 6 events, 23:59 can't be added."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[ScheduleEvent(i * 200, 20.0) for i in range(6)]
        )
        ws.days[2] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),  # Doesn't start at 00:00
            ]
        )

        result = apply_midnight_crossing(ws)
        # Monday should still have exactly 6 events (couldn't add 23:59)
        assert len(result.days[1].events) == 6

    def test_original_schedule_not_mutated(self):
        """apply_midnight_crossing should not mutate the input."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        ws.days[2] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
            ]
        )

        original_monday_count = len(ws.days[1].events)
        apply_midnight_crossing(ws)
        assert len(ws.days[1].events) == original_monday_count


# ── ZCL Payload Building ──────────────────────────────────────────────


class TestBuildZclPayloads:
    """Tests for build_zcl_set_weekly_payloads()."""

    def test_empty_schedule(self):
        ws = WeeklySchedule()
        payloads = build_zcl_set_weekly_payloads(ws)
        assert payloads == []

    def test_single_day(self):
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[  # Monday
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )

        payloads = build_zcl_set_weekly_payloads(ws)
        assert len(payloads) == 1
        p = payloads[0]
        assert p["num_transitions"] == 2
        assert p["day_of_week"] == SCHEDULE_DOW_MONDAY
        assert p["mode"] == SCHEDULE_MODE_HEAT
        assert p["transitions"] == [(360, 2100), (1320, 1800)]

    def test_multiple_days(self):
        ws = WeeklySchedule()
        ws.days[0] = DaySchedule(events=[ScheduleEvent(480, 20.0)])  # Sunday
        ws.days[6] = DaySchedule(events=[ScheduleEvent(480, 22.0)])  # Saturday

        payloads = build_zcl_set_weekly_payloads(ws)
        assert len(payloads) == 2
        assert payloads[0]["day_of_week"] == SCHEDULE_DOW_SUNDAY
        assert payloads[1]["day_of_week"] == SCHEDULE_DOW_SATURDAY

    def test_skip_empty_days(self):
        ws = WeeklySchedule()
        ws.days[3] = DaySchedule(events=[ScheduleEvent(720, 21.5)])  # Wednesday only
        payloads = build_zcl_set_weekly_payloads(ws)
        assert len(payloads) == 1
        # Check it's Wednesday
        assert payloads[0]["day_of_week"] == 0x08  # SCHEDULE_DOW_WEDNESDAY

    def test_setpoint_conversion(self):
        """Temperatures should be converted to x100 integers."""
        ws = WeeklySchedule()
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(0, 5.0),
                ScheduleEvent(720, 20.5),
                ScheduleEvent(1320, 17.0),
            ]
        )

        payloads = build_zcl_set_weekly_payloads(ws)
        transitions = payloads[0]["transitions"]
        assert transitions[0] == (0, 500)
        assert transitions[1] == (720, 2050)
        assert transitions[2] == (1320, 1700)


# ── ZCL Response Parsing ──────────────────────────────────────────────


class TestParseZclResponse:
    """Tests for parse_zcl_get_weekly_response()."""

    def test_single_day(self):
        transitions = [(360, 2100), (1320, 1800)]
        result = parse_zcl_get_weekly_response(
            SCHEDULE_DOW_MONDAY, SCHEDULE_MODE_HEAT, transitions
        )
        assert 1 in result  # Monday = index 1
        day = result[1]
        assert len(day.events) == 2
        assert day.events[0].minutes_since_midnight == 360
        assert day.events[0].temperature == 21.0
        assert day.events[1].minutes_since_midnight == 1320
        assert day.events[1].temperature == 18.0

    def test_empty_transitions(self):
        result = parse_zcl_get_weekly_response(
            SCHEDULE_DOW_MONDAY, SCHEDULE_MODE_HEAT, []
        )
        assert 1 in result
        assert result[1].is_empty

    def test_multi_day_mask(self):
        """A bitmask covering multiple days applies the same transitions to each."""
        mask = SCHEDULE_DOW_MONDAY | SCHEDULE_DOW_TUESDAY
        transitions = [(480, 2100)]
        result = parse_zcl_get_weekly_response(mask, SCHEDULE_MODE_HEAT, transitions)
        assert 1 in result  # Monday
        assert 2 in result  # Tuesday
        assert len(result[1].events) == 1
        assert len(result[2].events) == 1


# ── Schedule Comparison ───────────────────────────────────────────────


class TestSchedulesMatch:
    """Tests for schedules_match()."""

    def test_identical_schedules(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        assert schedules_match(ws1, ws2)

    def test_both_empty(self):
        assert schedules_match(WeeklySchedule(), WeeklySchedule())

    def test_different_event_count(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(720, 18.0),
            ]
        )
        assert not schedules_match(ws1, ws2)

    def test_different_time(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(events=[ScheduleEvent(480, 21.0)])
        assert not schedules_match(ws1, ws2)

    def test_different_temperature(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(events=[ScheduleEvent(360, 22.0)])
        assert not schedules_match(ws1, ws2)

    def test_within_tolerance(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.005)])
        assert schedules_match(ws1, ws2, tolerance=0.01)

    def test_outside_tolerance(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws1.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        ws2.days[1] = DaySchedule(events=[ScheduleEvent(360, 21.02)])
        assert not schedules_match(ws1, ws2, tolerance=0.01)

    def test_one_empty_day_vs_events(self):
        ws1 = WeeklySchedule()
        ws2 = WeeklySchedule()
        ws2.days[3] = DaySchedule(events=[ScheduleEvent(360, 21.0)])
        assert not schedules_match(ws1, ws2)


# ── from_ha_schedule ──────────────────────────────────────────────────


class TestFromHaSchedule:
    """Tests for converting HA schedule helper data to WeeklySchedule."""

    def test_single_block_single_day(self):
        """One at-home block on Monday."""
        blocks = [
            {"from": "06:00:00", "to": "08:00:00", "days": ["mon"]},
        ]
        schedule = from_ha_schedule(blocks, at_home_temp=21.0, away_temp=17.0)

        # Monday = day index 1
        assert len(schedule.days[1].events) == 2
        assert schedule.days[1].events[0] == ScheduleEvent(360, 21.0)
        assert schedule.days[1].events[1] == ScheduleEvent(480, 17.0)

        # Other days should be empty
        for i in [0, 2, 3, 4, 5, 6]:
            assert schedule.days[i].is_empty

    def test_multiple_blocks_single_day(self):
        """Two at-home blocks on Monday."""
        blocks = [
            {"from": "06:00:00", "to": "08:00:00", "days": ["mon"]},
            {"from": "16:00:00", "to": "22:00:00", "days": ["mon"]},
        ]
        schedule = from_ha_schedule(blocks, at_home_temp=21.0, away_temp=17.0)

        assert len(schedule.days[1].events) == 4
        assert schedule.days[1].events[0] == ScheduleEvent(360, 21.0)
        assert schedule.days[1].events[1] == ScheduleEvent(480, 17.0)
        assert schedule.days[1].events[2] == ScheduleEvent(960, 21.0)
        assert schedule.days[1].events[3] == ScheduleEvent(1320, 17.0)

    def test_block_across_multiple_days(self):
        """One block applied to Mon-Fri."""
        blocks = [
            {
                "from": "07:00:00",
                "to": "09:00:00",
                "days": ["mon", "tue", "wed", "thu", "fri"],
            },
        ]
        schedule = from_ha_schedule(blocks, at_home_temp=22.0, away_temp=18.0)

        for day_idx in [1, 2, 3, 4, 5]:  # Mon-Fri
            assert len(schedule.days[day_idx].events) == 2
            assert schedule.days[day_idx].events[0] == ScheduleEvent(420, 22.0)
            assert schedule.days[day_idx].events[1] == ScheduleEvent(540, 18.0)

        # Weekend should be empty
        assert schedule.days[0].is_empty  # Sunday
        assert schedule.days[6].is_empty  # Saturday

    def test_too_many_blocks_raises(self):
        """More than 3 blocks on a day should raise ValueError."""
        blocks = [
            {"from": "06:00:00", "to": "07:00:00", "days": ["mon"]},
            {"from": "08:00:00", "to": "09:00:00", "days": ["mon"]},
            {"from": "10:00:00", "to": "11:00:00", "days": ["mon"]},
            {"from": "12:00:00", "to": "13:00:00", "days": ["mon"]},
        ]
        with pytest.raises(ValueError, match="exceeds maximum"):
            from_ha_schedule(blocks, at_home_temp=21.0, away_temp=17.0)

    def test_block_too_short_raises(self):
        """Block shorter than 30 minutes should raise ValueError."""
        blocks = [
            {"from": "06:00:00", "to": "06:20:00", "days": ["mon"]},
        ]
        with pytest.raises(ValueError, match="minimum"):
            from_ha_schedule(blocks, at_home_temp=21.0, away_temp=17.0)

    def test_empty_schedule(self):
        """No blocks should produce empty schedule."""
        schedule = from_ha_schedule([], at_home_temp=21.0, away_temp=17.0)
        assert schedule.is_empty

    def test_time_without_seconds(self):
        """Time strings without seconds should work."""
        blocks = [
            {"from": "06:00", "to": "08:00", "days": ["sun"]},
        ]
        schedule = from_ha_schedule(blocks, at_home_temp=21.0, away_temp=17.0)
        assert len(schedule.days[0].events) == 2


# ── WeeklySchedule.from_dict edge cases ───────────────────────────────


class TestFromDictEdgeCases:
    """Tests for WeeklySchedule.from_dict with excess days."""

    def test_from_dict_more_than_7_days(self):
        """Days beyond the 7th are silently ignored (break at i >= 7)."""
        data = {
            "days": [
                [{"time": i * 60, "temp": 20.0}]
                for i in range(10)  # 10 day entries
            ]
        }
        ws = WeeklySchedule.from_dict(data)
        # Only the first 7 days should be populated
        for i in range(7):
            assert len(ws.days[i].events) == 1
            assert ws.days[i].events[0].minutes_since_midnight == i * 60
        # Schedule should still have exactly 7 days
        assert len(ws.days) == 7


# ── Midnight crossing: next day at max transitions ────────────────────


class TestMidnightCrossingMaxNextDay:
    """Tests for apply_midnight_crossing when next day is at max transitions."""

    def test_cannot_add_0000_next_day_at_max(self, caplog):
        """Warning logged when next day already has max transitions."""
        ws = WeeklySchedule()
        # Monday: one event whose carry-over temp needs bridging
        ws.days[1] = DaySchedule(
            events=[
                ScheduleEvent(360, 21.0),
                ScheduleEvent(1320, 18.0),
            ]
        )
        # Tuesday: starts at 06:00 (not 00:00) and already has 6 transitions
        ws.days[2] = DaySchedule(
            events=[ScheduleEvent(360 + i * 60, 20.0) for i in range(6)]
        )

        import logging

        with caplog.at_level(logging.WARNING):
            result = apply_midnight_crossing(ws)

        # The warning about being unable to add 00:00 should be logged
        assert any(
            "cannot add 00:00 event" in msg and "max transitions" in msg
            for msg in caplog.messages
        )
        # Tuesday should still have exactly 6 events (no 00:00 added)
        assert len(result.days[2].events) == 6
        assert all(ev.minutes_since_midnight != 0 for ev in result.days[2].events)
