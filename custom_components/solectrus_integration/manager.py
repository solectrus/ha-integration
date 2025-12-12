"""Manage state listeners and periodic writes to InfluxDB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .api import SolectrusInfluxClient, SolectrusInfluxError

MIN_WRITE_GAP = timedelta(seconds=5)
MAX_WRITE_GAP = timedelta(minutes=5)

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
    last_sent_at: datetime | None = None


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
        self._unsub_interval = None

    async def async_start(self) -> None:
        """Start listening for state updates."""
        initial_sensors: list[ConfiguredSensor] = []
        for sensor in self._sensors.values():
            current_state = self._hass.states.get(sensor.entity_id)
            value = self._state_to_value(current_state)
            if value is not None:
                sensor.last_value = value
                initial_sensors.append(sensor)

        entity_ids = [sensor.entity_id for sensor in self._sensors.values()]
        if entity_ids:
            self._unsub_state = async_track_state_change_event(
                self._hass,
                entity_ids,
                self._handle_state_change,
            )
        self._unsub_interval = async_track_time_interval(
            self._hass, self._handle_interval, timedelta(minutes=1)
        )

        for sensor in initial_sensors:
            await self._maybe_send(sensor, sensor.last_value)

    async def async_stop(self) -> None:
        """Stop listeners and timers."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

    @callback
    async def _handle_state_change(self, event: Event) -> None:
        """Handle a new state."""
        entity_id = event.data["entity_id"]
        sensor = self._sensors.get(entity_id)
        if sensor is None:
            return

        new_state: State | None = event.data.get("new_state")
        value = self._state_to_value(new_state)
        if value is None:
            return

        sensor.last_value = value
        await self._maybe_send(sensor, value)

    async def _handle_interval(self, _now: datetime) -> None:
        """Ensure values are sent at least every MAX_WRITE_GAP."""
        now = dt_util.utcnow()
        for sensor in self._sensors.values():
            if sensor.last_value is None:
                continue
            if sensor.last_value == 0:
                # Skip repeating zero values.
                continue
            if sensor.last_sent_at is None or (
                now - sensor.last_sent_at >= MAX_WRITE_GAP
            ):
                await self._send(sensor, sensor.last_value, now)

    async def _maybe_send(self, sensor: ConfiguredSensor, value: Any) -> None:
        """Send value if throttling allows."""
        now = dt_util.utcnow()
        if (
            sensor.last_sent_at is not None
            and now - sensor.last_sent_at < MIN_WRITE_GAP
        ):
            return
        await self._send(sensor, value, now)

    async def _send(
        self,
        sensor: ConfiguredSensor,
        value: Any,
        timestamp: datetime,
    ) -> None:
        """Write a value to InfluxDB."""
        value = self._coerce_value(value, sensor.data_type)
        if value is None:
            return
        try:
            await self._client.async_write(
                measurement=sensor.measurement,
                field=sensor.field,
                value=value,
            )
            sensor.last_sent_at = timestamp
        except SolectrusInfluxError:
            # Error already logged inside the client.
            return

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
