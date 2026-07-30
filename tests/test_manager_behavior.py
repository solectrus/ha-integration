"""Behavior tests for SensorManager: queue, flush, heartbeat, state changes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from influxdb_client import Point

from custom_components.solectrus.api import SolectrusInfluxError
from custom_components.solectrus.manager import (
    MAX_PENDING_POINTS,
    ConfiguredSensor,
    PendingPoint,
    SensorManager,
)

FIXED_NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


def _sensor(**overrides) -> ConfiguredSensor:
    defaults = {
        "key": "INVERTER_POWER",
        "entity_id": "sensor.inverter_power",
        "measurement": "inverter",
        "field": "power",
        "data_type": "int",
    }
    return ConfiguredSensor(**{**defaults, **overrides})


def _manager(sensors=None, client=None) -> SensorManager:
    hass = MagicMock()
    if client is None:
        client = MagicMock()
        client.async_write_batch = AsyncMock()
    if not isinstance(getattr(client, "async_get_field_types", None), AsyncMock):
        client.async_get_field_types = AsyncMock(return_value={})
    sensor_map = {}
    if sensors:
        for s in sensors:
            sensor_map[s.entity_id] = s
    return SensorManager(hass, client, sensor_map)


class TestQueuePoint:
    """Tests for _queue_point."""

    def test_adds_point_to_pending(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1
        key = f"INVERTER_POWER:{FIXED_NOW.isoformat()}"
        assert key in mgr._pending
        assert mgr._pending[key].value == 1500

    def test_overwrites_same_key(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)
        mgr._queue_point(sensor, 2000, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1
        key = f"INVERTER_POWER:{FIXED_NOW.isoformat()}"
        assert mgr._pending[key].value == 2000

    def test_custom_pending_key(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 100, timestamp=FIXED_NOW, pending_key="custom:key")

        assert "custom:key" in mgr._pending

    def test_coerces_value(self):
        sensor = _sensor(data_type="float")
        mgr = _manager([sensor])

        mgr._queue_point(sensor, "3.14", timestamp=FIXED_NOW)

        point = next(iter(mgr._pending.values()))
        assert point.value == 3.14

    def test_skips_invalid_value(self):
        sensor = _sensor(data_type="int")
        mgr = _manager([sensor])

        mgr._queue_point(sensor, "not_a_number", timestamp=FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_eviction_removes_oldest(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        # Fill buffer to MAX_PENDING_POINTS
        base = FIXED_NOW
        for i in range(MAX_PENDING_POINTS):
            ts = base + timedelta(seconds=i)
            mgr._pending[f"key:{i}"] = PendingPoint(
                sensor=sensor, value=i, timestamp=ts
            )

        assert len(mgr._pending) == MAX_PENDING_POINTS

        # Adding a new point should evict the oldest (key:0)
        new_ts = base + timedelta(seconds=MAX_PENDING_POINTS)
        mgr._queue_point(sensor, 9999, timestamp=new_ts)

        assert len(mgr._pending) == MAX_PENDING_POINTS
        assert "key:0" not in mgr._pending

    def test_overwrite_does_not_evict(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        base = FIXED_NOW
        for i in range(MAX_PENDING_POINTS):
            ts = base + timedelta(seconds=i)
            mgr._pending[f"INVERTER_POWER:{ts.isoformat()}"] = PendingPoint(
                sensor=sensor, value=i, timestamp=ts
            )

        # Overwriting an existing key should NOT trigger eviction
        first_ts = base
        mgr._queue_point(sensor, 9999, timestamp=first_ts)

        assert len(mgr._pending) == MAX_PENDING_POINTS

    def test_new_point_not_self_evicted(self):
        """A point with an old timestamp should not evict itself."""
        sensor = _sensor()
        mgr = _manager([sensor])

        # Fill with points all at a later time
        future = FIXED_NOW + timedelta(hours=1)
        for i in range(MAX_PENDING_POINTS):
            ts = future + timedelta(seconds=i)
            mgr._pending[f"key:{i}"] = PendingPoint(
                sensor=sensor, value=i, timestamp=ts
            )

        # Add a point with a timestamp OLDER than all existing ones
        old_ts = FIXED_NOW
        mgr._queue_point(sensor, 42, timestamp=old_ts)

        # The new point should be present (not self-evicted)
        new_key = f"INVERTER_POWER:{old_ts.isoformat()}"
        assert new_key in mgr._pending
        assert mgr._pending[new_key].value == 42


class TestFlushBatch:
    """Tests for _flush_batch."""

    @pytest.mark.asyncio
    async def test_sends_all_pending_points(self):
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        sensor = _sensor()
        mgr = _manager([sensor], client=client)

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)
        mgr._queue_point(sensor, 2000, timestamp=FIXED_NOW + timedelta(seconds=5))

        await mgr._flush_batch(FIXED_NOW)

        client.async_write_batch.assert_awaited_once()
        points = client.async_write_batch.call_args[0][0]
        assert len(points) == 2
        assert all(isinstance(p, Point) for p in points)
        assert mgr._pending == {}

    @pytest.mark.asyncio
    async def test_noop_when_empty(self):
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager(client=client)

        await mgr._flush_batch(FIXED_NOW)

        client.async_write_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_on_error(self):
        client = MagicMock()
        client.async_write_batch = AsyncMock(
            side_effect=SolectrusInfluxError("write failed")
        )
        sensor = _sensor()
        mgr = _manager([sensor], client=client)

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        await mgr._flush_batch(FIXED_NOW)

        # Points should be re-queued for retry
        assert len(mgr._pending) == 1

    @pytest.mark.asyncio
    async def test_retry_preserves_newer_values(self):
        """Newer values queued during a failed write are preserved over old ones."""
        client = MagicMock()
        sensor = _sensor()
        mgr = _manager([sensor], client=client)

        ts = FIXED_NOW
        key = f"INVERTER_POWER:{ts.isoformat()}"
        mgr._queue_point(sensor, 1500, timestamp=ts)

        async def write_side_effect(_points):
            # Simulate a new point arriving during the write
            mgr._pending[key] = PendingPoint(sensor=sensor, value=2000, timestamp=ts)
            raise SolectrusInfluxError("write failed")

        client.async_write_batch = AsyncMock(side_effect=write_side_effect)

        await mgr._flush_batch(FIXED_NOW)

        # The newer value (2000) should be preserved, not overwritten by the old (1500)
        assert mgr._pending[key].value == 2000

    @pytest.mark.asyncio
    async def test_logs_each_point_at_debug_level(self, caplog):
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        sensor = _sensor()
        mgr = _manager([sensor], client=client)

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        with caplog.at_level(logging.DEBUG):
            await mgr._flush_batch(FIXED_NOW)

        debug_messages = [
            r.message for r in caplog.records if r.levelno == logging.DEBUG
        ]
        point_logs = [m for m in debug_messages if m.startswith("Influx point:")]
        assert len(point_logs) == 1
        assert "sensor=INVERTER_POWER" in point_logs[0]
        assert "measurement=inverter" in point_logs[0]
        assert "field=power" in point_logs[0]
        assert "value=1500" in point_logs[0]
        assert any("succeeded: 1 point" in m for m in debug_messages)

    @pytest.mark.asyncio
    async def test_logs_points_even_when_write_fails(self, caplog):
        client = MagicMock()
        client.async_write_batch = AsyncMock(
            side_effect=SolectrusInfluxError("write failed")
        )
        sensor = _sensor()
        mgr = _manager([sensor], client=client)

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        with caplog.at_level(logging.DEBUG):
            await mgr._flush_batch(FIXED_NOW)

        debug_messages = [
            r.message for r in caplog.records if r.levelno == logging.DEBUG
        ]
        assert any(m.startswith("Influx point:") for m in debug_messages)
        assert any("batch write failed" in m for m in debug_messages)


class TestHandleStateChange:
    """Tests for _handle_state_change."""

    def _make_event(self, entity_id, new_state_value, last_updated=None):
        """Create a mock state change event."""
        if last_updated is None:
            last_updated = FIXED_NOW

        new_state = MagicMock()
        new_state.state = str(new_state_value)
        new_state.attributes = {}
        new_state.last_updated = last_updated

        event = MagicMock()
        event.data = {"entity_id": entity_id, "new_state": new_state}
        return event

    @pytest.mark.asyncio
    async def test_queues_point_for_known_sensor(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 1500)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == 1500

    @pytest.mark.asyncio
    async def test_ignores_unknown_entity(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        event = self._make_event("sensor.unknown", 42)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 0

    @pytest.mark.asyncio
    async def test_ignores_unavailable_state(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", "unavailable")
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 0

    @pytest.mark.asyncio
    async def test_skips_duplicate_value_and_timestamp(self):
        sensor = _sensor()
        sensor.last_value = 1500
        sensor.last_timestamp = FIXED_NOW
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 1500, last_updated=FIXED_NOW)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 0

    @pytest.mark.asyncio
    async def test_queues_when_value_changes(self):
        sensor = _sensor()
        sensor.last_value = 1500
        sensor.last_timestamp = FIXED_NOW
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 2000, last_updated=FIXED_NOW)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1

    @pytest.mark.asyncio
    async def test_queues_when_timestamp_changes(self):
        sensor = _sensor()
        sensor.last_value = 1500
        sensor.last_timestamp = FIXED_NOW
        mgr = _manager([sensor])

        new_ts = FIXED_NOW + timedelta(seconds=5)
        event = self._make_event("sensor.inverter_power", 1500, last_updated=new_ts)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1

    @pytest.mark.asyncio
    async def test_updates_last_value_and_timestamp(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 1500, last_updated=FIXED_NOW)
        await mgr._handle_state_change(event)

        assert sensor.last_value == 1500
        assert sensor.last_timestamp == FIXED_NOW

    @pytest.mark.asyncio
    async def test_gap_fill_inserts_zero_before_resume(self):
        sensor = _sensor()
        sensor.last_value = 0
        sensor.last_timestamp = FIXED_NOW - timedelta(minutes=5)
        mgr = _manager([sensor])

        new_ts = FIXED_NOW
        event = self._make_event("sensor.inverter_power", 1500, last_updated=new_ts)
        await mgr._handle_state_change(event)

        # Should have 2 points: gap-fill zero at ts-1s, and actual value
        assert len(mgr._pending) == 2
        points = sorted(mgr._pending.values(), key=lambda p: p.timestamp)
        assert points[0].value == 0
        assert points[0].timestamp == FIXED_NOW - timedelta(seconds=1)
        assert points[1].value == 1500
        assert points[1].timestamp == FIXED_NOW

    @pytest.mark.asyncio
    async def test_no_gap_fill_for_short_gap(self):
        sensor = _sensor()
        sensor.last_value = 0
        sensor.last_timestamp = FIXED_NOW - timedelta(seconds=10)
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 1500, last_updated=FIXED_NOW)
        await mgr._handle_state_change(event)

        # Short gap: only 1 point, no gap-fill
        assert len(mgr._pending) == 1

    @pytest.mark.asyncio
    async def test_no_gap_fill_when_last_value_nonzero(self):
        sensor = _sensor()
        sensor.last_value = 500
        sensor.last_timestamp = FIXED_NOW - timedelta(minutes=5)
        mgr = _manager([sensor])

        event = self._make_event("sensor.inverter_power", 1500, last_updated=FIXED_NOW)
        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1

    @pytest.mark.asyncio
    async def test_forecast_sensor_delegates(self):
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        mgr = _manager([sensor])

        new_state = MagicMock()
        new_state.attributes = {
            "forecast": [
                {"datetime": "2024-06-15T13:00:00+00:00", "power": 1500},
                {"datetime": "2024-06-15T14:00:00+00:00", "power": 1800},
            ]
        }

        event = MagicMock()
        event.data = {"entity_id": "sensor.forecast", "new_state": new_state}

        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 2

    @pytest.mark.asyncio
    async def test_forecast_sensor_reads_pvnode_power_key(self):
        """pvnode's power forecast attribute uses "watts", not "power"."""
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        mgr = _manager([sensor])

        new_state = MagicMock()
        new_state.attributes = {
            "forecast": [
                {"datetime": "2024-06-15T13:00:00+00:00", "watts": 1500},
                {"datetime": "2024-06-15T14:00:00+00:00", "watts": 1800},
            ]
        }

        event = MagicMock()
        event.data = {"entity_id": "sensor.forecast", "new_state": new_state}

        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 2
        values = {point.value for point in mgr._pending.values()}
        assert values == {1500, 1800}

    @pytest.mark.asyncio
    async def test_forecast_sensor_reads_pvnode_clearsky_key(self):
        """pvnode's clear-sky forecast attribute uses "watts_clearsky"."""
        sensor = _sensor(
            key="INVERTER_POWER_FORECAST_CLEARSKY", entity_id="sensor.forecast_clearsky"
        )
        mgr = _manager([sensor])

        new_state = MagicMock()
        new_state.attributes = {
            "forecast": [
                {"datetime": "2024-06-15T13:00:00+00:00", "watts_clearsky": 2000},
            ]
        }

        event = MagicMock()
        event.data = {"entity_id": "sensor.forecast_clearsky", "new_state": new_state}

        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == 2000

    @pytest.mark.asyncio
    async def test_forecast_sensor_reads_solcast_detailed_forecast(self):
        """ha-solcast-solar uses period_start (datetime) / pv_estimate (kW)."""
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        mgr = _manager([sensor])

        new_state = MagicMock()
        new_state.attributes = {
            # Solcast's time series lives under "detailedForecast"/
            # "detailedHourly", not "forecast" - left empty here to prove
            # the fallback attribute name is actually used.
            "forecast": [],
            "detailedForecast": [
                {
                    "period_start": datetime(2024, 6, 15, 13, 0, 0, tzinfo=UTC),
                    "pv_estimate": 1.5,
                },
            ],
        }

        event = MagicMock()
        event.data = {"entity_id": "sensor.forecast", "new_state": new_state}

        await mgr._handle_state_change(event)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == 1500.0


class TestHeartbeat:
    """Tests for _heartbeat."""

    def test_requeues_current_values(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        mock_state = MagicMock()
        mock_state.state = "1500"
        mock_state.attributes = {}
        mock_state.last_updated = FIXED_NOW
        mgr._hass.states.get.return_value = mock_state

        with patch(
            "custom_components.solectrus.manager.dt_util.utcnow",
            return_value=FIXED_NOW,
        ):
            mgr._heartbeat(FIXED_NOW)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == 1500
        assert sensor.last_value == 1500

    def test_skips_unavailable_sensors(self):
        sensor = _sensor()
        mgr = _manager([sensor])

        mock_state = MagicMock()
        mock_state.state = "unavailable"
        mgr._hass.states.get.return_value = mock_state

        with patch(
            "custom_components.solectrus.manager.dt_util.utcnow",
            return_value=FIXED_NOW,
        ):
            mgr._heartbeat(FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_skips_forecast_sensors(self):
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        mgr = _manager([sensor])
        mgr._hass.states.get.return_value = MagicMock(state="1500")

        with patch(
            "custom_components.solectrus.manager.dt_util.utcnow",
            return_value=FIXED_NOW,
        ):
            mgr._heartbeat(FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_handles_multiple_sensors(self):
        sensor1 = _sensor(key="INVERTER_POWER", entity_id="sensor.inverter")
        sensor2 = _sensor(
            key="HOUSE_POWER",
            entity_id="sensor.house",
            measurement="house",
        )
        mgr = _manager([sensor1, sensor2])

        def mock_get(entity_id):
            state = MagicMock()
            state.state = "1500" if entity_id == "sensor.inverter" else "800"
            state.attributes = {}
            state.last_updated = FIXED_NOW
            return state

        mgr._hass.states.get.side_effect = mock_get

        with patch(
            "custom_components.solectrus.manager.dt_util.utcnow",
            return_value=FIXED_NOW,
        ):
            mgr._heartbeat(FIXED_NOW)

        assert len(mgr._pending) == 2


class TestAsyncStartStop:
    """Tests for async_start and async_stop."""

    @pytest.mark.asyncio
    async def test_start_registers_listeners(self):
        sensor = _sensor()
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)

        mock_state = MagicMock()
        mock_state.state = "unavailable"
        mgr._hass.states.get.return_value = mock_state

        with (
            patch(
                "custom_components.solectrus.manager.async_track_state_change_event"
            ) as mock_track_state,
            patch(
                "custom_components.solectrus.manager.async_track_time_interval"
            ) as mock_track_time,
        ):
            mock_track_state.return_value = MagicMock()
            mock_track_time.return_value = MagicMock()

            await mgr.async_start()

        mock_track_state.assert_called_once()
        assert mock_track_time.call_count == 2  # batch + heartbeat

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_listeners(self):
        sensor = _sensor()
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)

        unsub_state = MagicMock()
        unsub_batch = MagicMock()
        unsub_heartbeat = MagicMock()
        mgr._unsub_state = unsub_state
        mgr._unsub_batch = unsub_batch
        mgr._unsub_heartbeat = unsub_heartbeat

        await mgr.async_stop()

        unsub_state.assert_called_once()
        unsub_batch.assert_called_once()
        unsub_heartbeat.assert_called_once()
        assert mgr._unsub_state is None
        assert mgr._unsub_batch is None
        assert mgr._unsub_heartbeat is None

    @pytest.mark.asyncio
    async def test_stop_flushes_remaining_points(self):
        sensor = _sensor()
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        await mgr.async_stop()

        client.async_write_batch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_queues_initial_values(self):
        sensor = _sensor()
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)

        mock_state = MagicMock()
        mock_state.state = "1500"
        mock_state.attributes = {}
        mock_state.last_updated = FIXED_NOW
        mgr._hass.states.get.return_value = mock_state

        with (
            patch(
                "custom_components.solectrus.manager.async_track_state_change_event",
                return_value=MagicMock(),
            ),
            patch(
                "custom_components.solectrus.manager.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            await mgr.async_start()

        # Initial values should have been flushed
        client.async_write_batch.assert_awaited_once()
        points = client.async_write_batch.call_args[0][0]
        assert len(points) == 1

    @pytest.mark.asyncio
    async def test_start_seeds_forecast_from_current_state(self):
        """Forecast sensors must not wait for their next state change to seed.

        Sources like pvnode can poll on a long interval, so relying solely on
        the next state_changed event can leave InfluxDB empty for a long time
        after every restart even though the source already has data now.
        """
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)

        mock_state = MagicMock()
        mock_state.attributes = {
            "forecast": [
                {"datetime": "2024-06-15T13:00:00+00:00", "watts": 1500},
                {"datetime": "2024-06-15T14:00:00+00:00", "watts": 1800},
            ]
        }
        mgr._hass.states.get.return_value = mock_state

        with (
            patch(
                "custom_components.solectrus.manager.async_track_state_change_event",
                return_value=MagicMock(),
            ),
            patch(
                "custom_components.solectrus.manager.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            await mgr.async_start()

        client.async_write_batch.assert_awaited_once()
        points = client.async_write_batch.call_args[0][0]
        assert len(points) == 2

    @pytest.mark.asyncio
    async def test_start_skips_forecast_seed_when_state_missing(self):
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        mgr = _manager([sensor], client=client)
        mgr._hass.states.get.return_value = None

        with (
            patch(
                "custom_components.solectrus.manager.async_track_state_change_event",
                return_value=MagicMock(),
            ),
            patch(
                "custom_components.solectrus.manager.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            await mgr.async_start()

        client.async_write_batch.assert_not_called()


class TestResolveDataTypes:
    """Tests for SensorManager._resolve_data_types."""

    @pytest.mark.asyncio
    async def test_adopts_existing_influx_type(self):
        sensor = _sensor(data_type="int")
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        client.async_get_field_types = AsyncMock(
            return_value={("inverter", "power"): "float"}
        )
        mgr = _manager([sensor], client=client)

        await mgr._resolve_data_types()

        assert sensor.data_type == "float"

    @pytest.mark.asyncio
    async def test_keeps_fallback_when_no_data(self):
        sensor = _sensor(data_type="int")
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        client.async_get_field_types = AsyncMock(return_value={})
        mgr = _manager([sensor], client=client)

        await mgr._resolve_data_types()

        assert sensor.data_type == "int"

    @pytest.mark.asyncio
    async def test_runs_before_first_flush(self):
        """async_start must resolve types before queuing initial values."""
        sensor = _sensor(data_type="int")
        client = MagicMock()
        client.async_write_batch = AsyncMock()
        client.async_get_field_types = AsyncMock(
            return_value={("inverter", "power"): "float"}
        )
        mgr = _manager([sensor], client=client)

        mock_state = MagicMock()
        mock_state.state = "1500"
        mock_state.attributes = {}
        mock_state.last_updated = FIXED_NOW
        mgr._hass.states.get.return_value = mock_state

        with (
            patch(
                "custom_components.solectrus.manager.async_track_state_change_event",
                return_value=MagicMock(),
            ),
            patch(
                "custom_components.solectrus.manager.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            await mgr.async_start()

        points = client.async_write_batch.call_args[0][0]
        assert len(points) == 1
        # Influx line protocol marks ints with an `i` suffix; floats have none.
        line = points[0].to_line_protocol()
        assert "power=1500" in line
        assert "power=1500i" not in line


class TestWeatherTemperatureSeries:
    """Tests for _weather_temperature_series."""

    @pytest.mark.asyncio
    async def test_parses_valid_response(self):
        mgr = _manager()
        mgr._hass.services.async_call = AsyncMock(
            return_value={
                "weather.home": {
                    "forecast": [
                        {"datetime": "2024-06-15T12:00:00+00:00", "temperature": 22.5},
                        {"datetime": "2024-06-15T13:00:00+00:00", "temperature": 23.0},
                    ]
                }
            }
        )

        result = await mgr._weather_temperature_series("weather.home")

        assert len(result) == 2
        assert result[0][1] == 22.5
        assert result[1][1] == 23.0

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        mgr = _manager()
        mgr._hass.services.async_call = AsyncMock(side_effect=RuntimeError("fail"))

        result = await mgr._weather_temperature_series("weather.home")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_invalid_response(self):
        mgr = _manager()
        mgr._hass.services.async_call = AsyncMock(return_value="not a dict")

        result = await mgr._weather_temperature_series("weather.home")

        assert result == []


class TestValueRangeValidation:
    """Tests for out-of-range value rejection in _queue_point."""

    def test_negative_power_discarded(self):
        sensor = _sensor(min_value=0)
        mgr = _manager([sensor])

        mgr._queue_point(sensor, -100, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_zero_power_accepted(self):
        sensor = _sensor(min_value=0)
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 0, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1

    def test_positive_power_accepted(self):
        sensor = _sensor(min_value=0)
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 1500, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == 1500

    def test_soc_above_100_discarded(self):
        sensor = _sensor(
            key="BATTERY_SOC",
            entity_id="sensor.battery_soc",
            measurement="battery",
            field="soc",
            data_type="float",
            min_value=0,
            max_value=100,
        )
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 105.0, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_soc_below_zero_discarded(self):
        sensor = _sensor(
            key="BATTERY_SOC",
            entity_id="sensor.battery_soc",
            measurement="battery",
            field="soc",
            data_type="float",
            min_value=0,
            max_value=100,
        )
        mgr = _manager([sensor])

        mgr._queue_point(sensor, -1.0, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 0

    def test_soc_in_range_accepted(self):
        sensor = _sensor(
            key="BATTERY_SOC",
            entity_id="sensor.battery_soc",
            measurement="battery",
            field="soc",
            data_type="float",
            min_value=0,
            max_value=100,
        )
        mgr = _manager([sensor])

        mgr._queue_point(sensor, 75.5, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1

    def test_sensor_without_limits_accepts_negative(self):
        sensor = _sensor(
            key="OUTDOOR_TEMP",
            entity_id="sensor.outdoor_temp",
            measurement="outdoor",
            field="temperature",
            data_type="float",
        )
        mgr = _manager([sensor])

        mgr._queue_point(sensor, -10.5, timestamp=FIXED_NOW)

        assert len(mgr._pending) == 1
        point = next(iter(mgr._pending.values()))
        assert point.value == -10.5

    def test_warning_logged_once_per_sensor(self, caplog):
        sensor = _sensor(min_value=0)
        mgr = _manager([sensor])

        with caplog.at_level(logging.WARNING):
            mgr._queue_point(sensor, -100, timestamp=FIXED_NOW)
            mgr._queue_point(sensor, -200, timestamp=FIXED_NOW + timedelta(seconds=5))

        warning_messages = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_messages) == 1
        assert "out of range" in warning_messages[0].message
        assert sensor.entity_id in warning_messages[0].message


class TestCheckAvailability:
    """Tests for _check_availability."""

    def test_warns_when_state_missing(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, None)

        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "missing" in warnings[0]
        assert sensor.entity_id in warnings[0]
        assert sensor.key in mgr._unavailable_sensors

    def test_warns_when_unavailable(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        state = MagicMock(state=STATE_UNAVAILABLE)

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, state)

        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "unavailable" in warnings[0]

    def test_warns_when_unknown(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        state = MagicMock(state=STATE_UNKNOWN)

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, state)

        warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "unknown" in warnings[0]

    def test_no_warning_for_available_state(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        state = MagicMock(state="1500")

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, state)

        assert not any(r.levelno == logging.WARNING for r in caplog.records)
        assert sensor.key not in mgr._unavailable_sensors

    def test_warns_only_once_while_still_unavailable(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        state = MagicMock(state=STATE_UNAVAILABLE)

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, state)
            mgr._check_availability(sensor, state)
            mgr._check_availability(sensor, state)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_logs_recovery_once_available_again(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        unavailable = MagicMock(state=STATE_UNAVAILABLE)
        recovered = MagicMock(state="1500")

        with caplog.at_level(logging.INFO):
            mgr._check_availability(sensor, unavailable)
            mgr._check_availability(sensor, recovered)

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_messages) == 1
        assert "reporting again" in info_messages[0]
        assert sensor.key not in mgr._unavailable_sensors

    def test_warns_again_after_recovering_and_dropping_again(self, caplog):
        """Each new outage should be reported, not just the first ever one."""
        sensor = _sensor()
        mgr = _manager([sensor])
        unavailable = MagicMock(state=STATE_UNAVAILABLE)
        recovered = MagicMock(state="1500")

        with caplog.at_level(logging.WARNING):
            mgr._check_availability(sensor, unavailable)
            mgr._check_availability(sensor, recovered)
            mgr._check_availability(sensor, unavailable)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    @pytest.mark.asyncio
    async def test_handle_state_change_warns_and_skips_point(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])

        event = MagicMock()
        event.data = {
            "entity_id": sensor.entity_id,
            "new_state": MagicMock(state=STATE_UNAVAILABLE),
        }

        with caplog.at_level(logging.WARNING):
            await mgr._handle_state_change(event)

        assert any(
            r.levelno == logging.WARNING and "unavailable" in r.message
            for r in caplog.records
        )
        assert len(mgr._pending) == 0

    @pytest.mark.asyncio
    async def test_handle_state_change_warns_for_forecast_sensor(self, caplog):
        sensor = _sensor(key="INVERTER_POWER_FORECAST", entity_id="sensor.forecast")
        mgr = _manager([sensor])

        event = MagicMock()
        event.data = {
            "entity_id": sensor.entity_id,
            "new_state": MagicMock(state=STATE_UNAVAILABLE, attributes={}),
        }

        with caplog.at_level(logging.WARNING):
            await mgr._handle_state_change(event)

        assert any(
            r.levelno == logging.WARNING and "unavailable" in r.message
            for r in caplog.records
        )

    def test_heartbeat_does_not_repeat_warning(self, caplog):
        sensor = _sensor()
        mgr = _manager([sensor])
        mgr._hass.states.get.return_value = MagicMock(state=STATE_UNAVAILABLE)

        with caplog.at_level(logging.WARNING):
            mgr._heartbeat(FIXED_NOW)
            mgr._heartbeat(FIXED_NOW)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
