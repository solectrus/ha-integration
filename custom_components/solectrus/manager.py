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
from .const import (
    DATA_TYPE_BOOL,
    DATA_TYPE_FLOAT,
    DATA_TYPE_INT,
    DATA_TYPE_STRING,
    FORECAST_ATTRIBUTE_NAMES,
    FORECAST_ATTRIBUTE_VALUE_KEYS,
    FORECAST_SENSOR_KEYS,
    LOGGER,
)

BATCH_INTERVAL = timedelta(seconds=5)
HEARTBEAT_INTERVAL = timedelta(minutes=5)
GAP_FILL_ZERO_RESUME_THRESHOLD = timedelta(seconds=30)
MAX_PENDING_POINTS = 10_000

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
    return round(float(value))


SIMPLE_CONVERTERS: dict[str, Any] = {
    DATA_TYPE_INT: _coerce_int,
    DATA_TYPE_FLOAT: float,
    DATA_TYPE_STRING: str,
}


@dataclass
class ConfiguredSensor:
    """A single configured SOLECTRUS sensor mapping."""

    key: str
    entity_id: str
    measurement: str
    field: str
    data_type: str
    min_value: float | None = None
    max_value: float | None = None
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
        self._unsub_heartbeat = None
        self._pending: dict[str, PendingPoint] = {}
        self._warned_out_of_range: set[str] = set()
        self._unavailable_sensors: set[str] = set()

    async def async_start(self) -> None:
        """Start listening for state updates."""
        # Must run before any writes: Influx freezes the field type on first
        # write, so we have to match existing data or be rejected.
        await self._resolve_data_types()

        # Queue initial values
        for sensor in self._sensors.values():
            current_state = self._hass.states.get(sensor.entity_id)
            self._check_availability(sensor, current_state)
            if sensor.key in FORECAST_SENSOR_KEYS:
                # Seed immediately from whatever the source already has,
                # instead of waiting for its next state change - which, for
                # slow-polling sources, can otherwise leave InfluxDB empty
                # for a long time after every HA/integration restart.
                await self._queue_forecast_points(sensor, current_state)
                continue
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
        self._unsub_heartbeat = async_track_time_interval(
            self._hass, self._heartbeat, HEARTBEAT_INTERVAL
        )

        # Send initial batch immediately
        await self._flush_batch(dt_util.utcnow())

    async def _resolve_data_types(self) -> None:
        """Adopt the types already present in InfluxDB for configured fields."""
        if not self._sensors:
            return
        # Multiple sensor keys can share one (measurement, field) — dedupe.
        fields = sorted({(s.measurement, s.field) for s in self._sensors.values()})
        # Auth errors degrade gracefully (empty dict) inside the API layer;
        # transient errors propagate so HA can retry setup with ConfigEntryNotReady.
        existing = await self._client.async_get_field_types(fields)

        for sensor in self._sensors.values():
            detected = existing.get((sensor.measurement, sensor.field))
            if detected is None:
                LOGGER.debug(
                    "No prior data for %s.%s; using fallback type %s",
                    sensor.measurement,
                    sensor.field,
                    sensor.data_type,
                )
                continue
            if detected == sensor.data_type:
                continue
            LOGGER.info(
                "Adopting Influx field type for %s.%s: %s (fallback was %s)",
                sensor.measurement,
                sensor.field,
                detected,
                sensor.data_type,
            )
            sensor.data_type = detected

    async def async_stop(self) -> None:
        """Stop listeners and timers."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_batch:
            self._unsub_batch()
            self._unsub_batch = None
        if self._unsub_heartbeat:
            self._unsub_heartbeat()
            self._unsub_heartbeat = None
        # Flush remaining points
        if self._pending:
            await self._flush_batch(dt_util.utcnow())

    def _check_availability(
        self,
        sensor: ConfiguredSensor,
        state: State | None,
    ) -> None:
        """
        Warn once when a sensor becomes unknown/unavailable/missing.

        Also logs once when it starts reporting again, so outages are
        visible in the log even though no points are written for them
        (see _state_to_value).
        """
        if state is None:
            status = "missing"
        elif state.state == STATE_UNAVAILABLE:
            status = "unavailable"
        elif state.state == STATE_UNKNOWN:
            status = "unknown"
        else:
            status = None

        was_unavailable = sensor.key in self._unavailable_sensors
        if status is not None:
            if not was_unavailable:
                self._unavailable_sensors.add(sensor.key)
                LOGGER.warning(
                    "%s (%s) is %s; no new points will be written until it "
                    "reports a value again",
                    sensor.key,
                    sensor.entity_id,
                    status,
                )
        elif was_unavailable:
            self._unavailable_sensors.discard(sensor.key)
            LOGGER.info("%s (%s) is reporting again", sensor.key, sensor.entity_id)

    async def _handle_state_change(self, event: Event) -> None:
        """Handle a new state."""
        entity_id = event.data["entity_id"]
        sensor = self._sensors.get(entity_id)
        if sensor is None:
            return

        new_state: State | None = event.data.get("new_state")
        self._check_availability(sensor, new_state)
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

    def _heartbeat(self, _now: datetime) -> None:
        """
        Periodically re-queue all current sensor values.

        This ensures continuous data points in InfluxDB even for sensors
        that remain unchanged (e.g., constantly reporting 0).
        """
        timestamp = self._normalize_timestamp(dt_util.utcnow())

        for sensor in self._sensors.values():
            if sensor.key in FORECAST_SENSOR_KEYS:
                continue

            current_state = self._hass.states.get(sensor.entity_id)
            self._check_availability(sensor, current_state)
            value = self._state_to_value(current_state)
            if value is None:
                continue

            coerced = self._coerce_value(value, sensor.data_type)
            if coerced is None:
                continue

            sensor.last_value = coerced
            sensor.last_timestamp = timestamp
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

        if isinstance(coerced, (int, float)):
            below = sensor.min_value is not None and coerced < sensor.min_value
            above = sensor.max_value is not None and coerced > sensor.max_value
            if below or above:
                if sensor.key not in self._warned_out_of_range:
                    LOGGER.warning(
                        "Value %s from %s is out of range (%s..%s), discarded; "
                        "check the sensor configuration in Home Assistant",
                        coerced,
                        sensor.entity_id,
                        sensor.min_value if sensor.min_value is not None else "",
                        sensor.max_value if sensor.max_value is not None else "",
                    )
                    self._warned_out_of_range.add(sensor.key)
                return

        normalized_timestamp = self._normalize_timestamp(timestamp or dt_util.utcnow())
        key = pending_key or f"{sensor.key}:{normalized_timestamp.isoformat()}"

        # Evict oldest entry before adding when buffer is full (only for new keys).
        if key not in self._pending and len(self._pending) >= MAX_PENDING_POINTS:
            oldest_key = min(self._pending, key=lambda k: self._pending[k].timestamp)
            del self._pending[oldest_key]

        self._pending[key] = PendingPoint(
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
            value_key = FORECAST_ATTRIBUTE_VALUE_KEYS.get(sensor.key, (sensor.field,))
            series = self._attribute_forecast_series(
                self._forecast_attribute_list(state),
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
    def _forecast_attribute_list(state: State) -> Any:
        """Return the first non-empty forecast time series attribute, if any."""
        for name in FORECAST_ATTRIBUTE_NAMES:
            candidate = state.attributes.get(name)
            if isinstance(candidate, list) and candidate:
                return candidate
        return None

    @staticmethod
    def _normalize_value_keys(
        value_key: str | tuple[str | tuple[str, float], ...],
    ) -> list[tuple[str, float]]:
        """Normalize the value_key shorthand forms into (key, multiplier) pairs."""
        if isinstance(value_key, str):
            return [(value_key, 1)]
        return [
            entry if isinstance(entry, tuple) else (entry, 1) for entry in value_key
        ]

    @staticmethod
    def _attribute_forecast_series(
        forecast_list: Any,
        *,
        value_key: str | tuple[str | tuple[str, float], ...],
    ) -> list[tuple[datetime, Any]]:
        if not isinstance(forecast_list, list):
            return []

        value_keys = SensorManager._normalize_value_keys(value_key)

        series: list[tuple[datetime, Any]] = []
        for item in forecast_list:
            if not isinstance(item, dict):
                continue

            raw_time = None
            for key in ("datetime", "time", "period_end", "period_start"):
                candidate = item.get(key)
                if candidate:
                    raw_time = candidate
                    break
            if raw_time is None:
                continue

            value = None
            for key, multiplier in value_keys:
                candidate = item.get(key)
                if candidate is not None:
                    value = candidate * multiplier if multiplier != 1 else candidate
                    break
            if value is None:
                continue

            # Some sources (e.g. ha-solcast-solar) already provide a datetime
            # object here instead of an ISO string.
            when = (
                raw_time
                if isinstance(raw_time, datetime)
                else dt_util.parse_datetime(raw_time)
            )
            if when is not None:
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
            # Logged before the write attempt so it's visible even on failure -
            # enable debug logging for this integration to see exactly what
            # gets sent to InfluxDB.
            LOGGER.debug(
                "Influx point: sensor=%s measurement=%s field=%s value=%r timestamp=%s",
                item.sensor.key,
                item.sensor.measurement,
                item.sensor.field,
                item.value,
                item.timestamp.isoformat(),
            )

        try:
            await self._client.async_write_batch(points)
        except SolectrusInfluxError as err:
            # Keep pending for next attempt; preserve newer values already queued.
            for key, item in pending.items():
                self._pending.setdefault(key, item)
            LOGGER.debug(
                "Influx batch write failed; keeping %d points for retry: %s",
                len(self._pending),
                err,
            )
        else:
            LOGGER.debug("Influx batch write succeeded: %d point(s) sent", len(points))

    @staticmethod
    def _coerce_value(value: Any, data_type: str) -> Any | None:
        """Coerce value to the configured datatype."""
        try:
            if data_type in SIMPLE_CONVERTERS:
                coerced: Any | None = SIMPLE_CONVERTERS[data_type](value)
            elif data_type == DATA_TYPE_BOOL:
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
