"""Tests for the SensorManager utility functions."""

from datetime import UTC, datetime

from custom_components.solectrus_integration.manager import (
    SensorManager,
    _coerce_int,
)


class TestCoerceInt:
    """Tests for _coerce_int helper."""

    def test_int_value(self):
        assert _coerce_int(42) == 42

    def test_float_rounds_up(self):
        assert _coerce_int(42.7) == 43

    def test_float_rounds_down(self):
        assert _coerce_int(42.3) == 42

    def test_float_rounds_half(self):
        assert _coerce_int(42.5) == 42  # Python rounds .5 to nearest even
        assert _coerce_int(43.5) == 44

    def test_string_int(self):
        assert _coerce_int("123") == 123

    def test_string_float_rounds(self):
        assert _coerce_int("123.9") == 124
        assert _coerce_int("123.4") == 123


class TestCoerceValue:
    """Tests for SensorManager._coerce_value."""

    def test_int_from_int(self):
        assert SensorManager._coerce_value(42, "int") == 42

    def test_int_from_float(self):
        assert SensorManager._coerce_value(42.7, "int") == 43
        assert SensorManager._coerce_value(42.3, "int") == 42

    def test_int_from_string(self):
        assert SensorManager._coerce_value("123", "int") == 123

    def test_float_from_int(self):
        assert SensorManager._coerce_value(42, "float") == 42.0

    def test_float_from_string(self):
        assert SensorManager._coerce_value("3.14", "float") == 3.14

    def test_string_from_int(self):
        assert SensorManager._coerce_value(42, "string") == "42"

    def test_string_passthrough(self):
        assert SensorManager._coerce_value("hello", "string") == "hello"

    def test_bool_from_true_string(self):
        assert SensorManager._coerce_value("on", "bool") is True
        assert SensorManager._coerce_value("true", "bool") is True
        assert SensorManager._coerce_value("yes", "bool") is True
        assert SensorManager._coerce_value("1", "bool") is True

    def test_bool_from_false_string(self):
        assert SensorManager._coerce_value("off", "bool") is False
        assert SensorManager._coerce_value("false", "bool") is False
        assert SensorManager._coerce_value("no", "bool") is False
        assert SensorManager._coerce_value("0", "bool") is False

    def test_bool_from_bool(self):
        assert SensorManager._coerce_value(True, "bool") is True
        assert SensorManager._coerce_value(False, "bool") is False

    def test_bool_from_number(self):
        assert SensorManager._coerce_value(1, "bool") is True
        assert SensorManager._coerce_value(0, "bool") is False

    def test_bool_case_insensitive(self):
        assert SensorManager._coerce_value("ON", "bool") is True
        assert SensorManager._coerce_value("OFF", "bool") is False

    def test_invalid_bool_string(self):
        assert SensorManager._coerce_value("maybe", "bool") is None

    def test_invalid_int(self):
        assert SensorManager._coerce_value("not_a_number", "int") is None

    def test_unknown_type_passthrough(self):
        assert SensorManager._coerce_value("value", "unknown") == "value"


class TestStateToValue:
    """Tests for SensorManager._state_to_value using simple mock objects."""

    class MockState:
        """Simple mock for Home Assistant State."""

        def __init__(self, state: str):
            self.state = state

    def test_none_state(self):
        assert SensorManager._state_to_value(None) is None

    def test_unknown_state(self):
        state = self.MockState("unknown")
        assert SensorManager._state_to_value(state) is None

    def test_unavailable_state(self):
        state = self.MockState("unavailable")
        assert SensorManager._state_to_value(state) is None

    def test_integer_string(self):
        state = self.MockState("42")
        assert SensorManager._state_to_value(state) == 42

    def test_float_string(self):
        state = self.MockState("3.14")
        assert SensorManager._state_to_value(state) == 3.14

    def test_on_state(self):
        state = self.MockState("on")
        assert SensorManager._state_to_value(state) is True

    def test_off_state(self):
        state = self.MockState("off")
        assert SensorManager._state_to_value(state) is False

    def test_true_state(self):
        state = self.MockState("true")
        assert SensorManager._state_to_value(state) is True

    def test_false_state(self):
        state = self.MockState("false")
        assert SensorManager._state_to_value(state) is False

    def test_string_state(self):
        state = self.MockState("heating")
        assert SensorManager._state_to_value(state) == "heating"


class TestAttributeForecastSeries:
    """Tests for SensorManager._attribute_forecast_series."""

    def test_empty_list(self):
        result = SensorManager._attribute_forecast_series([], value_key="power")
        assert result == []

    def test_none_input(self):
        result = SensorManager._attribute_forecast_series(None, value_key="power")
        assert result == []

    def test_valid_forecast(self, sample_forecast_list):
        result = SensorManager._attribute_forecast_series(
            sample_forecast_list, value_key="power"
        )
        assert len(result) == 3
        assert result[0][1] == 1500
        assert result[1][1] == 1800
        assert result[2][1] == 1200

    def test_temperature_key(self, sample_forecast_list):
        result = SensorManager._attribute_forecast_series(
            sample_forecast_list, value_key="temperature"
        )
        assert len(result) == 3
        assert result[0][1] == 5.0
        assert result[1][1] == 6.5

    def test_alternative_time_keys(self):
        forecast = [
            {"time": "2024-01-15T12:00:00+00:00", "power": 100},
            {"period_end": "2024-01-15T13:00:00+00:00", "power": 200},
        ]
        result = SensorManager._attribute_forecast_series(forecast, value_key="power")
        assert len(result) == 2

    def test_missing_value_key(self):
        forecast = [
            {"datetime": "2024-01-15T12:00:00+00:00", "other": 100},
        ]
        result = SensorManager._attribute_forecast_series(forecast, value_key="power")
        assert result == []

    def test_invalid_items_skipped(self):
        forecast = [
            {"datetime": "2024-01-15T12:00:00+00:00", "power": 100},
            "invalid",
            None,
            {"datetime": "2024-01-15T13:00:00+00:00", "power": 200},
        ]
        result = SensorManager._attribute_forecast_series(forecast, value_key="power")
        assert len(result) == 2


class TestNormalizeTimestamp:
    """Tests for SensorManager._normalize_timestamp."""

    def test_removes_microseconds(self):
        ts = datetime(2024, 1, 15, 12, 30, 45, 123456, tzinfo=UTC)
        result = SensorManager._normalize_timestamp(ts)
        assert result.microsecond == 0
        assert result.second == 45

    def test_converts_to_utc(self):
        cet = UTC  # Simplified; uses UTC for test
        ts = datetime(2024, 1, 15, 12, 30, 45, tzinfo=cet)
        result = SensorManager._normalize_timestamp(ts)
        assert result.tzinfo == UTC


class TestStateToTimestamp:
    """Tests for SensorManager._state_to_timestamp."""

    class MockState:
        """Mock for Home Assistant State with attributes."""

        def __init__(self, attributes=None, last_updated=None):
            self.attributes = attributes or {}
            self.last_updated = last_updated or datetime(
                2024, 1, 15, 10, 0, 0, tzinfo=UTC
            )

    def test_unix_seconds_vs_milliseconds(self):
        # Values <= 10^12 are treated as seconds
        state_sec = self.MockState(attributes={"timestamp": 1705321800})
        assert SensorManager._state_to_timestamp(state_sec) == datetime(
            2024, 1, 15, 12, 30, 0, tzinfo=UTC
        )

        # Values > 10^12 are treated as milliseconds
        state_ms = self.MockState(attributes={"timestamp": 1705321800000})
        assert SensorManager._state_to_timestamp(state_ms) == datetime(
            2024, 1, 15, 12, 30, 0, tzinfo=UTC
        )

    def test_fallback_on_invalid_timestamp(self):
        last_updated = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        state = self.MockState(
            attributes={"timestamp": "not-a-date"},
            last_updated=last_updated,
        )
        assert SensorManager._state_to_timestamp(state) == last_updated
