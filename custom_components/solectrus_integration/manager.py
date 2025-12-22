"""Manage state listeners and periodic writes to InfluxDB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from influxdb_client import Point, WritePrecision

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant, State

from .api import SolectrusInfluxClient, SolectrusInfluxError
from .const import FORECAST_SENSOR_KEYS, LOGGER

BATCH_INTERVAL = timedelta(seconds=5)
GAP_FILL_ZERO_RESUME_THRESHOLD = timedelta(seconds=30)

BOOL_STRING_MAP: dict[str, bool] = {
    "on": True,
    "true": True,
    "1": True,
    "yes": True,
    "off": False,
    "false": False,
    "0": False,
    "no": False,
}


def _coerce_int(value: Any) -> int:
    return int(float(value))


SIMPLE_CONVERTERS: dict[str, Any] = {
    "int": _coerce_int,
    "float": float,
    "string": str,
}


@dataclass
class ConfiguredSensor:
    """A single configured SOLECTRUS sensor mapping."""

    key: str
    entity_id: str
    measurement: str
    field: str
    data_type: str
    last_value: Any | None = None
    last_timestamp: datetime | None = None


@dataclass
class PendingPoint:
    """A point waiting to be sent."""

    sensor: ConfiguredSensor
    value: Any
    timestamp: datetime


class SensorManager:
    """Listen for state changes and push values to InfluxDB."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SolectrusInfluxClient,
        sensors: dict[str, ConfiguredSensor],
    ) -> None:
        """Initialize the manager."""
        self._hass = hass
        self._client = client
        self._sensors = sensors
        self._unsub_state = None
        self._unsub_batch = None
        self._pending: dict[str, PendingPoint] = {}

    async def async_start(self) -> None:
        """Start listening for state updates."""
        # Queue initial values
        for sensor in self._sensors.values():
            if sensor.key in FORECAST_SENSOR_KEYS:
                continue
            current_state = self._hass.states.get(sensor.entity_id)
            value = self._state_to_value(current_state)
            if value is not None:
                timestamp = self._normalize_timestamp(
                    self._state_to_timestamp(current_state) or dt_util.utcnow()
                )
                coerced = self._coerce_value(value, sensor.data_type)
                if coerced is None:
                    continue
                sensor.last_value = coerced
                sensor.last_timestamp = timestamp
                self._queue_point(sensor, coerced, timestamp=timestamp)

        entity_ids = [sensor.entity_id for sensor in self._sensors.values()]
        if entity_ids:
            self._unsub_state = async_track_state_change_event(
                self._hass,
                entity_ids,
                self._handle_state_change,
            )
        self._unsub_batch = async_track_time_interval(
            self._hass, self._flush_batch, BATCH_INTERVAL
        )

        # Send initial batch immediately
        await self._flush_batch(dt_util.utcnow())

    async def async_stop(self) -> None:
        """Stop listeners and timers."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_batch:
            self._unsub_batch()
            self._unsub_batch = None
        # Flush remaining points
        if self._pending:
            await self._flush_batch(dt_util.utcnow())

    async def _handle_state_change(self, event: Event) -> None:
        """Handle a new state."""
        entity_id = event.data["entity_id"]
        sensor = self._sensors.get(entity_id)
        if sensor is None:
            return

        new_state: State | None = event.data.get("new_state")
        if sensor.key in FORECAST_SENSOR_KEYS:
            await self._queue_forecast_points(sensor, new_state)
            return

        value = self._state_to_value(new_state)
        if value is None:
            return

        timestamp = self._normalize_timestamp(
            self._state_to_timestamp(new_state) or dt_util.utcnow()
        )
        coerced = self._coerce_value(value, sensor.data_type)
        if coerced is None:
            return

        # Only skip if both value and timestamp are unchanged.
        if sensor.last_value == coerced and sensor.last_timestamp == timestamp:
            return

        # Avoid long-gap interpolation artifacts:
        # if we previously wrote 0 and nothing arrived for a while, then a positive
        # value comes in, insert an extra 0 point 1s before the new value.
        should_gap_fill = False
        if (
            sensor.last_timestamp is not None
            and isinstance(sensor.last_value, (int, float))
            and sensor.last_value == 0
            and isinstance(coerced, (int, float))
            and coerced > 0
        ):
            gap = dt_util.as_utc(timestamp) - dt_util.as_utc(sensor.last_timestamp)
            should_gap_fill = gap >= GAP_FILL_ZERO_RESUME_THRESHOLD

        sensor.last_value = coerced
        sensor.last_timestamp = timestamp
        if should_gap_fill:
            self._queue_point(sensor, 0, timestamp=timestamp - timedelta(seconds=1))
        self._queue_point(sensor, coerced, timestamp=timestamp)

    def _queue_point(
        self,
        sensor: ConfiguredSensor,
        value: Any,
        *,
        timestamp: datetime | None = None,
        pending_key: str | None = None,
    ) -> None:
        """Add a point to the pending batch, overwriting any previous value."""
        coerced = self._coerce_value(value, sensor.data_type)
        if coerced is None:
            return

        normalized_timestamp = self._normalize_timestamp(timestamp or dt_util.utcnow())
        self._pending[
            pending_key or f"{sensor.key}:{normalized_timestamp.isoformat()}"
        ] = PendingPoint(
            sensor=sensor,
            value=coerced,
            timestamp=normalized_timestamp,
        )

    async def _queue_forecast_points(
        self,
        sensor: ConfiguredSensor,
        state: State | None,
    ) -> None:
        """Queue forecast time series points for the whitelisted forecast keys."""
        if state is None:
            return

        if sensor.entity_id.startswith("weather."):
            series = await self._weather_temperature_series(sensor.entity_id)
        else:
            value_key = (
                "temperature" if sensor.key == "OUTDOOR_TEMP_FORECAST" else sensor.field
            )
            series = self._attribute_forecast_series(
                state.attributes.get("forecast"),
                value_key=value_key,
            )

        for timestamp, value in sorted(series, key=lambda pair: pair[0]):
            normalized_timestamp = self._normalize_timestamp(timestamp)
            self._queue_point(
                sensor,
                value,
                timestamp=normalized_timestamp,
                pending_key=f"{sensor.key}:{normalized_timestamp.isoformat()}",
            )

    async def _weather_temperature_series(
        self,
        entity_id: str,
    ) -> list[tuple[datetime, Any]]:
        try:
            response = await self._hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception:  # noqa: BLE001
            return []

        if not isinstance(response, dict):
            return []

        entity_payload = response.get(entity_id)
        if not isinstance(entity_payload, dict):
            return []

        forecast_list = entity_payload.get("forecast")
        if not isinstance(forecast_list, list):
            return []

        series: list[tuple[datetime, Any]] = []
        for item in forecast_list:
            if not isinstance(item, dict):
                continue
            raw_time = item.get("datetime")
            if not raw_time:
                continue
            when = dt_util.parse_datetime(raw_time)
            value = item.get("temperature")
            if when is not None and value is not None:
                series.append((dt_util.as_utc(when), value))

        return series

    @staticmethod
    def _attribute_forecast_series(
        forecast_list: Any,
        *,
        value_key: str,
    ) -> list[tuple[datetime, Any]]:
        if not isinstance(forecast_list, list):
            return []

        series: list[tuple[datetime, Any]] = []
        for item in forecast_list:
            if not isinstance(item, dict):
                continue

            raw_time = None
            for key in ("datetime", "time", "period_end"):
                candidate = item.get(key)
                if candidate:
                    raw_time = candidate
                    break
            if raw_time is None:
                continue

            when = dt_util.parse_datetime(raw_time)
            value = item.get(value_key)
            if when is not None and value is not None:
                series.append((dt_util.as_utc(when), value))

        return series

    async def _flush_batch(self, _now: datetime) -> None:
        """Send all pending points as a batch."""
        if not self._pending:
            return

        pending = self._pending
        self._pending = {}

        points: list[Point] = []
        for item in pending.values():
            point = Point(item.sensor.measurement)
            point.field(item.sensor.field, item.value)
            point.time(item.timestamp, WritePrecision.S)
            points.append(point)

        try:
            await self._client.async_write_batch(points)
        except SolectrusInfluxError as err:
            # Keep pending for next attempt; preserve newer values already queued.
            for key, item in pending.items():
                self._pending.setdefault(key, item)
            LOGGER.debug(
                "Influx batch write failed; keeping points for retry: %s",
                err,
                exc_info=True,
            )

    @staticmethod
    def _coerce_value(value: Any, data_type: str) -> Any | None:
        """Coerce value to the configured datatype."""
        try:
            if data_type in SIMPLE_CONVERTERS:
                coerced: Any | None = SIMPLE_CONVERTERS[data_type](value)
            elif data_type == "bool":
                if isinstance(value, bool):
                    coerced = value
                elif isinstance(value, (int, float)):
                    coerced = bool(value)
                else:
                    coerced = (
                        BOOL_STRING_MAP.get(value.lower())
                        if isinstance(value, str)
                        else None
                    )
            else:
                coerced = value
        except (TypeError, ValueError):
            return None

        return coerced

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        """Normalize timestamps to match the write precision (seconds)."""
        return dt_util.as_utc(timestamp).replace(microsecond=0)

    @staticmethod
    def _state_to_timestamp(state: State | None) -> datetime | None:
        """Extract a timestamp from a state (attributes preferred)."""
        if state is None:
            return None

        # Prefer explicit source timestamps provided via state attributes.
        for key in (
            "timestamp",
            "time",
            "datetime",
            "period_end",
            "last_update",
            "last_updated",
        ):
            raw = state.attributes.get(key)
            if raw is None:
                continue

            if isinstance(raw, datetime):
                return dt_util.as_utc(raw)

            if isinstance(raw, (int, float)):
                # Heuristic: treat large values as milliseconds since epoch.
                seconds = float(raw) / 1000 if raw > 10**12 else float(raw)
                try:
                    return datetime.fromtimestamp(seconds, tz=UTC)
                except (OverflowError, OSError, ValueError):
                    continue

            if isinstance(raw, str):
                parsed = dt_util.parse_datetime(raw)
                if parsed is not None:
                    return dt_util.as_utc(parsed)

        return dt_util.as_utc(state.last_updated)

    @staticmethod
    def _state_to_value(state: State | None) -> Any | None:
        """Convert a Home Assistant state to an Influx friendly value."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        raw = state.state
        try:
            int_value = int(raw)
            if str(int_value) == raw:
                return int_value
        except ValueError:
            pass

        try:
            return float(raw)
        except ValueError:
            pass

        lowered = raw.lower()
        if lowered in {"on", "true"}:
            return True
        if lowered in {"off", "false"}:
            return False

        return raw
