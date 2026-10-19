"""Tests for the SOLECTRUS integration entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.solectrus import (
    _build_sensor_map,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.solectrus.api import SolectrusConnectionError
from custom_components.solectrus.const import (
    CONF_BUCKET,
    CONF_ENTITY_ID,
    CONF_FIELD,
    CONF_MEASUREMENT,
    CONF_ORG,
    CONF_SENSORS,
    CONF_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
)
from custom_components.solectrus.data import SolectrusRuntimeData
from custom_components.solectrus.manager import ConfiguredSensor


class TestBuildSensorMap:
    """Tests for _build_sensor_map."""

    def test_build_sensor_map_basic(self):
        """One sensor configured returns correct ConfiguredSensor."""
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "INVERTER_POWER": {
                    CONF_ENTITY_ID: "sensor.inverter_power",
                },
            },
        }

        result = _build_sensor_map(mock_entry)

        assert len(result) == 1
        assert "INVERTER_POWER" in result
        sensor = result["INVERTER_POWER"]
        assert isinstance(sensor, ConfiguredSensor)
        assert sensor.key == "INVERTER_POWER"
        assert sensor.entity_id == "sensor.inverter_power"

    def test_build_sensor_map_empty(self):
        """No sensors configured returns empty dict."""
        mock_entry = MagicMock()
        mock_entry.options = {CONF_SENSORS: {}}

        result = _build_sensor_map(mock_entry)

        assert result == {}

    def test_build_sensor_map_skips_empty_entity_id(self):
        """Entry with no entity_id is skipped."""
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "INVERTER_POWER": {
                    CONF_ENTITY_ID: "",
                },
            },
        }

        result = _build_sensor_map(mock_entry)

        assert result == {}

    def test_build_sensor_map_custom_overrides(self):
        """Custom measurement/field from options are used."""
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "INVERTER_POWER": {
                    CONF_ENTITY_ID: "sensor.my_inverter",
                    CONF_MEASUREMENT: "custom_measurement",
                    CONF_FIELD: "custom_field",
                },
            },
        }

        result = _build_sensor_map(mock_entry)

        sensor = result["INVERTER_POWER"]
        assert sensor.measurement == "custom_measurement"
        assert sensor.field == "custom_field"

    def test_build_sensor_map_defaults_for_known_sensor(self):
        """Known sensor (e.g. INVERTER_POWER) gets defaults from SENSOR_DEFINITIONS."""
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "INVERTER_POWER": {
                    CONF_ENTITY_ID: "sensor.inverter_power",
                },
            },
        }

        result = _build_sensor_map(mock_entry)

        sensor = result["INVERTER_POWER"]
        assert sensor.measurement == "inverter"
        assert sensor.field == "power"
        # data_type seeded from fallback; the manager replaces it on startup.
        assert sensor.data_type == "int"

    def test_build_sensor_map_sets_attribute_for_car_position(self):
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "CAR_LATITUDE_2": {CONF_ENTITY_ID: "device_tracker.car_2"},
                "CAR_BATTERY_SOC_2": {CONF_ENTITY_ID: "sensor.car_2_soc"},
            },
        }

        result = _build_sensor_map(mock_entry)

        assert result["CAR_LATITUDE_2"].attribute == "latitude"
        assert result["CAR_LATITUDE_2"].measurement == "car_2"
        assert result["CAR_BATTERY_SOC_2"].attribute is None

    def test_build_sensor_map_keeps_both_sensors_of_one_entity(self):
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "CAR_LATITUDE": {CONF_ENTITY_ID: "device_tracker.zoe"},
                "CAR_LONGITUDE": {CONF_ENTITY_ID: "device_tracker.zoe"},
            },
        }

        result = _build_sensor_map(mock_entry)

        assert set(result) == {"CAR_LATITUDE", "CAR_LONGITUDE"}
        assert result["CAR_LATITUDE"].attribute == "latitude"
        assert result["CAR_LONGITUDE"].attribute == "longitude"

    def test_build_sensor_map_skips_unknown_sensor(self):
        """Unknown sensor keys are skipped (only SENSOR_DEFINITIONS keys are valid)."""
        mock_entry = MagicMock()
        mock_entry.options = {
            CONF_SENSORS: {
                "TOTALLY_UNKNOWN_SENSOR": {
                    CONF_ENTITY_ID: "sensor.unknown",
                },
            },
        }

        result = _build_sensor_map(mock_entry)

        assert result == {}


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.mark.asyncio
    async def test_setup_entry_success(self):
        """Mocks client.async_connect() and SensorManager.async_start()."""
        hass = MagicMock()
        entry = MagicMock()
        entry.data = {
            CONF_URL: "http://localhost:8086",
            CONF_TOKEN: "test-token",
            CONF_ORG: "test-org",
            CONF_BUCKET: "test-bucket",
            CONF_VERIFY_SSL: True,
        }
        entry.options = {CONF_SENSORS: {}}
        entry.runtime_data = None
        entry.async_on_unload = MagicMock()

        mock_client = MagicMock()
        mock_client.async_connect = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.async_start = AsyncMock()

        with (
            patch(
                "custom_components.solectrus.SolectrusInfluxClient",
                return_value=mock_client,
            ),
            patch(
                "custom_components.solectrus.SensorManager",
                return_value=mock_manager,
            ),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        mock_client.async_connect.assert_awaited_once()
        mock_manager.async_start.assert_awaited_once()
        assert isinstance(entry.runtime_data, SolectrusRuntimeData)
        assert entry.runtime_data.client is mock_client
        assert entry.runtime_data.manager is mock_manager

    @pytest.mark.asyncio
    async def test_setup_entry_connection_error(self):
        """SolectrusConnectionError leads to ConfigEntryNotReady."""
        hass = MagicMock()
        entry = MagicMock()
        entry.data = {
            CONF_URL: "http://localhost:8086",
            CONF_TOKEN: "test-token",
            CONF_ORG: "test-org",
            CONF_BUCKET: "test-bucket",
            CONF_VERIFY_SSL: True,
        }
        entry.options = {CONF_SENSORS: {}}

        mock_client = MagicMock()
        mock_client.async_connect = AsyncMock(
            side_effect=SolectrusConnectionError("Connection refused"),
        )
        mock_client.async_close = AsyncMock()

        with (
            patch(
                "custom_components.solectrus.SolectrusInfluxClient",
                return_value=mock_client,
            ),
            pytest.raises(ConfigEntryNotReady, match="Cannot reach InfluxDB"),
        ):
            await async_setup_entry(hass, entry)

    @pytest.mark.asyncio
    async def test_setup_entry_cleans_up_on_failure(self):
        """Verify client.async_close() is called when connection fails."""
        hass = MagicMock()
        entry = MagicMock()
        entry.data = {
            CONF_URL: "http://localhost:8086",
            CONF_TOKEN: "test-token",
            CONF_ORG: "test-org",
            CONF_BUCKET: "test-bucket",
            CONF_VERIFY_SSL: True,
        }
        entry.options = {CONF_SENSORS: {}}

        mock_client = MagicMock()
        mock_client.async_connect = AsyncMock(
            side_effect=SolectrusConnectionError("timeout"),
        )
        mock_client.async_close = AsyncMock()

        with (
            patch(
                "custom_components.solectrus.SolectrusInfluxClient",
                return_value=mock_client,
            ),
            pytest.raises(Exception),  # noqa: B017, PT011
        ):
            await async_setup_entry(hass, entry)

        mock_client.async_close.assert_awaited_once()


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_entry(self):
        """Verifies manager.async_stop() and client.async_close() are called."""
        hass = MagicMock()
        mock_client = MagicMock()
        mock_client.async_close = AsyncMock()
        mock_manager = MagicMock()
        mock_manager.async_stop = AsyncMock()

        entry = MagicMock()
        entry.runtime_data = SolectrusRuntimeData(
            client=mock_client,
            manager=mock_manager,
        )

        result = await async_unload_entry(hass, entry)

        assert result is True
        mock_manager.async_stop.assert_awaited_once()
        mock_client.async_close.assert_awaited_once()
