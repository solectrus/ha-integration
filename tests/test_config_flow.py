"""Tests for the SOLECTRUS config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import section

from custom_components.solectrus.api import SolectrusInfluxError
from custom_components.solectrus.config_flow import (
    SolectrusConfigFlow,
    SolectrusOptionsFlowHandler,
    _build_sensors_schema,
    _influx_schema,
    _parse_sensors_input,
)
from custom_components.solectrus.const import (
    CAR_SENSOR_DEFINITIONS,
    CONF_BUCKET,
    CONF_ENTITY_ID,
    CONF_FIELD,
    CONF_MEASUREMENT,
    CONF_ORG,
    CONF_SENSORS,
    CONF_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    SENSOR_DEFINITIONS,
)

CAR_PREFIXES = tuple(CAR_SENSOR_DEFINITIONS)

VALID_USER_INPUT = {
    CONF_URL: "http://localhost:8086",
    CONF_TOKEN: "test-token",
    CONF_ORG: "test-org",
    CONF_BUCKET: "test-bucket",
    CONF_VERIFY_SSL: True,
}


def _mock_config_entry(data: dict | None = None, options: dict | None = None):
    """Create a mock config entry."""
    entry = MagicMock()
    entry.data = data or dict(VALID_USER_INPUT)
    entry.options = options or {}
    entry.title = "InfluxDB-Exporter"
    entry.entry_id = "test_entry_id"
    return entry


def _all_key_names(schema_dict: dict) -> set[str]:
    """Return the field names of a schema, including those inside sections."""
    names: set[str] = set()
    for key, value in schema_dict.items():
        if isinstance(value, section):
            names.update(str(k) for k in value.schema.schema)
        else:
            names.add(str(key))
    return names


def _mock_hass(entry=None):
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    return hass


class TestInfluxSchema:
    """Tests for the _influx_schema helper."""

    def test_schema_with_empty_defaults(self):
        schema = _influx_schema({})
        assert isinstance(schema, vol.Schema)
        # Validate that the schema accepts valid input
        result = schema(VALID_USER_INPUT)
        assert result[CONF_URL] == "http://localhost:8086"
        assert result[CONF_TOKEN] == "test-token"
        assert result[CONF_ORG] == "test-org"
        assert result[CONF_BUCKET] == "test-bucket"
        assert result[CONF_VERIFY_SSL] is True

    def test_schema_with_provided_defaults(self):
        defaults = {
            CONF_URL: "http://other:8086",
            CONF_TOKEN: "other-token",
            CONF_ORG: "other-org",
            CONF_BUCKET: "other-bucket",
            CONF_VERIFY_SSL: False,
        }
        schema = _influx_schema(defaults)
        # Schema keys should carry the defaults from the dict
        for key in schema.schema:
            if hasattr(key, "default") and key.default is not vol.UNDEFINED:
                if str(key) == CONF_URL:
                    assert key.default() == "http://other:8086"
                elif str(key) == CONF_VERIFY_SSL:
                    assert key.default() is False

    def test_schema_contains_all_required_keys(self):
        schema = _influx_schema({})
        key_names = {str(k) for k in schema.schema}
        assert CONF_URL in key_names
        assert CONF_TOKEN in key_names
        assert CONF_ORG in key_names
        assert CONF_BUCKET in key_names
        assert CONF_VERIFY_SSL in key_names


class TestBuildSensorsSchema:
    """Tests for the _build_sensors_schema helper."""

    def test_basic_mode_entity_keys_only(self):
        schema_dict = _build_sensors_schema({}, show_advanced=False)
        key_names = _all_key_names(schema_dict)
        # In basic mode, only entity keys should be present
        for sensor_key in SENSOR_DEFINITIONS:
            assert f"{sensor_key}_entity" in key_names
            assert f"{sensor_key}_measurement" not in key_names
            assert f"{sensor_key}_field" not in key_names
            assert f"{sensor_key}_data_type" not in key_names

    def test_advanced_mode_includes_extra_keys(self):
        schema_dict = _build_sensors_schema({}, show_advanced=True)
        key_names = _all_key_names(schema_dict)
        for sensor_key in SENSOR_DEFINITIONS:
            assert f"{sensor_key}_entity" in key_names
            assert f"{sensor_key}_measurement" in key_names
            assert f"{sensor_key}_field" in key_names
            # data_type is no longer surfaced in the UI; detected from Influx.
            assert f"{sensor_key}_data_type" not in key_names

    def test_existing_sensors_populate_defaults(self):
        existing = {
            "INVERTER_POWER": {
                CONF_ENTITY_ID: "sensor.inverter",
                CONF_MEASUREMENT: "custom_m",
                CONF_FIELD: "custom_f",
            },
        }
        schema_dict = _build_sensors_schema(existing, show_advanced=True)
        # Find the entity key for INVERTER_POWER and check its suggested_value / default
        for key in schema_dict:
            key_str = str(key)
            if key_str == "INVERTER_POWER_entity":
                assert key.description["suggested_value"] == "sensor.inverter"
            if key_str == "INVERTER_POWER_measurement" and hasattr(key, "default"):
                assert key.default() == "custom_m"
            if key_str == "INVERTER_POWER_field" and hasattr(key, "default"):
                assert key.default() == "custom_f"

    def test_car_sensors_are_grouped_in_sections(self):
        schema_dict = _build_sensors_schema({}, show_advanced=False)
        top_level = [str(k) for k in schema_dict]

        sections = [k for k in top_level if k.startswith("car_")]
        assert sections == ["car_1", "car_2", "car_3", "car_4", "car_5"]
        assert not any(k.startswith("CAR_") for k in top_level)
        # The sections come before the custom consumers
        assert top_level.index("car_5") < top_level.index("CUSTOM_01_entity")

        car_1 = {str(k) for k in schema_dict["car_1"].schema.schema}
        car_2 = {str(k) for k in schema_dict["car_2"].schema.schema}
        assert car_1 == {f"{prefix}_entity" for prefix in CAR_PREFIXES}
        assert car_2 == {f"{prefix}_2_entity" for prefix in CAR_PREFIXES}

    def test_car_section_expanded_only_when_mapped(self):
        existing = {"CAR_RANGE_2": {CONF_ENTITY_ID: "sensor.car_2_range"}}
        schema_dict = _build_sensors_schema(existing, show_advanced=False)

        collapsed = {
            str(k): v.options["collapsed"]
            for k, v in schema_dict.items()
            if isinstance(v, section)
        }
        assert collapsed == {
            "car_1": True,
            "car_2": False,
            "car_3": True,
            "car_4": True,
            "car_5": True,
        }


class TestCarSensorDefinitions:
    """Tests for the car sensors in SENSOR_DEFINITIONS."""

    def test_first_car_has_no_number(self):
        for prefix in CAR_PREFIXES:
            assert SENSOR_DEFINITIONS[prefix].measurement == "car"
            assert f"{prefix}_1" not in SENSOR_DEFINITIONS

    def test_other_cars_have_number_in_key_and_measurement(self):
        for number in range(2, 6):
            for prefix in CAR_PREFIXES:
                definition = SENSOR_DEFINITIONS[f"{prefix}_{number}"]
                assert definition.measurement == f"car_{number}"
                assert definition.field == SENSOR_DEFINITIONS[prefix].field
        assert "CAR_ODOMETER_6" not in SENSOR_DEFINITIONS

    def test_fields_match_solectrus(self):
        fields = {prefix: SENSOR_DEFINITIONS[prefix].field for prefix in CAR_PREFIXES}
        assert fields == {
            "CAR_BATTERY_SOC": "battery_soc",
            "CAR_ODOMETER": "odometer",
            "CAR_RANGE": "range",
            "CAR_CONNECTED": "connected",
            "CAR_LATITUDE": "latitude",
            "CAR_LONGITUDE": "longitude",
        }


class TestParseSensorsInput:
    """Tests for the _parse_sensors_input helper."""

    def test_empty_input_returns_empty(self):
        result = _parse_sensors_input({}, {}, show_advanced=False)
        assert result == {}

    def test_entity_id_only_basic_mode(self):
        user_input = {"INVERTER_POWER_entity": "sensor.inverter_power"}
        result = _parse_sensors_input(user_input, {}, show_advanced=False)
        assert "INVERTER_POWER" in result
        sensor = result["INVERTER_POWER"]
        assert sensor[CONF_ENTITY_ID] == "sensor.inverter_power"
        # Defaults from SENSOR_DEFINITIONS
        defn = SENSOR_DEFINITIONS["INVERTER_POWER"]
        assert sensor[CONF_MEASUREMENT] == defn.measurement
        assert sensor[CONF_FIELD] == defn.field
        assert "data_type" not in sensor

    def test_advanced_mode_overrides(self):
        user_input = {
            "INVERTER_POWER_entity": "sensor.inverter_power",
            "INVERTER_POWER_measurement": "custom_inverter",
            "INVERTER_POWER_field": "custom_power",
        }
        result = _parse_sensors_input(user_input, {}, show_advanced=True)
        sensor = result["INVERTER_POWER"]
        assert sensor[CONF_MEASUREMENT] == "custom_inverter"
        assert sensor[CONF_FIELD] == "custom_power"
        assert "data_type" not in sensor

    def test_no_entity_id_skips_sensor(self):
        user_input = {
            "INVERTER_POWER_entity": None,
            "HOUSE_POWER_entity": "sensor.house_power",
        }
        result = _parse_sensors_input(user_input, {}, show_advanced=False)
        assert "INVERTER_POWER" not in result
        assert "HOUSE_POWER" in result

    def test_existing_sensors_used_in_basic_mode(self):
        existing = {
            "INVERTER_POWER": {
                CONF_MEASUREMENT: "existing_m",
                CONF_FIELD: "existing_f",
            },
        }
        user_input = {"INVERTER_POWER_entity": "sensor.inverter_power"}
        result = _parse_sensors_input(user_input, existing, show_advanced=False)
        sensor = result["INVERTER_POWER"]
        # Basic mode uses existing sensor config, not defaults
        assert sensor[CONF_MEASUREMENT] == "existing_m"
        assert sensor[CONF_FIELD] == "existing_f"

    def test_advanced_mode_fallback_to_existing(self):
        existing = {
            "INVERTER_POWER": {
                CONF_MEASUREMENT: "existing_m",
                CONF_FIELD: "existing_f",
            },
        }
        # Advanced mode with empty overrides falls back to existing
        user_input = {
            "INVERTER_POWER_entity": "sensor.inverter_power",
            "INVERTER_POWER_measurement": "",
            "INVERTER_POWER_field": "",
        }
        result = _parse_sensors_input(user_input, existing, show_advanced=True)
        sensor = result["INVERTER_POWER"]
        assert sensor[CONF_MEASUREMENT] == "existing_m"
        assert sensor[CONF_FIELD] == "existing_f"

    def test_reads_fields_inside_car_sections(self):
        user_input = {
            "HOUSE_POWER_entity": "sensor.house",
            "car_1": {"CAR_BATTERY_SOC_entity": "sensor.car_soc"},
            "car_3": {
                "CAR_ODOMETER_3_entity": "sensor.car_3_odometer",
                "CAR_ODOMETER_3_measurement": "my_car",
            },
        }
        result = _parse_sensors_input(user_input, {}, show_advanced=True)

        assert result["CAR_BATTERY_SOC"] == {
            CONF_ENTITY_ID: "sensor.car_soc",
            CONF_MEASUREMENT: "car",
            CONF_FIELD: "battery_soc",
        }
        assert result["CAR_ODOMETER_3"] == {
            CONF_ENTITY_ID: "sensor.car_3_odometer",
            CONF_MEASUREMENT: "my_car",
            CONF_FIELD: "odometer",
        }
        assert set(result) == {"HOUSE_POWER", "CAR_BATTERY_SOC", "CAR_ODOMETER_3"}

    def test_multiple_sensors(self):
        user_input = {
            "INVERTER_POWER_entity": "sensor.inverter",
            "HOUSE_POWER_entity": "sensor.house",
            "BATTERY_SOC_entity": "sensor.battery",
        }
        result = _parse_sensors_input(user_input, {}, show_advanced=False)
        assert len(result) == 3
        assert "INVERTER_POWER" in result
        assert "HOUSE_POWER" in result
        assert "BATTERY_SOC" in result


class TestConfigFlowStepUser:
    """Tests for SolectrusConfigFlow.async_step_user."""

    @pytest.mark.asyncio
    async def test_user_form_shown_on_initial_call(self):
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass()

        result = await flow.async_step_user(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_user_success(self):
        hass = _mock_hass()
        hass.config_entries.async_entry_for_domain_unique_id.return_value = None

        flow = SolectrusConfigFlow()
        flow.hass = hass
        flow.context = {"source": "user"}
        flow._async_in_progress = MagicMock(return_value=[])

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock()
        mock_client.async_close = AsyncMock()

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_user(user_input=dict(VALID_USER_INPUT))

        assert result["type"] == "create_entry"
        assert result["title"] == "InfluxDB-Exporter"
        assert result["data"] == VALID_USER_INPUT
        mock_client.async_validate_connection.assert_awaited_once()
        mock_client.async_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_connection_error(self):
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass()

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock(
            side_effect=SolectrusInfluxError("Connection refused"),
        )
        mock_client.async_close = AsyncMock()

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_user(user_input=dict(VALID_USER_INPUT))

        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}
        assert result["description_placeholders"]["error"] == "Connection refused"
        mock_client.async_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_unexpected_error(self):
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass()

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock(
            side_effect=RuntimeError("Something went wrong"),
        )
        mock_client.async_close = AsyncMock()

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_user(user_input=dict(VALID_USER_INPUT))

        assert result["type"] == "form"
        assert result["errors"] == {"base": "unknown"}
        mock_client.async_close.assert_awaited_once()


class TestConfigFlowStepReconfigure:
    """Tests for SolectrusConfigFlow.async_step_reconfigure."""

    @pytest.mark.asyncio
    async def test_reconfigure_no_entry(self):
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=None)
        flow.context = {"entry_id": "nonexistent"}

        result = await flow.async_step_reconfigure(user_input=None)

        assert result["type"] == "abort"
        assert result["reason"] == "unknown"

    @pytest.mark.asyncio
    async def test_reconfigure_no_entry_id_in_context(self):
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=None)
        flow.context = {}

        result = await flow.async_step_reconfigure(user_input=None)

        assert result["type"] == "abort"
        assert result["reason"] == "unknown"

    @pytest.mark.asyncio
    async def test_reconfigure_form_shown_on_initial_call(self):
        entry = _mock_config_entry()
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=entry)
        flow.context = {"entry_id": entry.entry_id}

        result = await flow.async_step_reconfigure(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "reconfigure"
        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_reconfigure_success(self):
        entry = _mock_config_entry()
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=entry)
        flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock()
        mock_client.async_close = AsyncMock()

        updated_input = {
            **VALID_USER_INPUT,
            CONF_URL: "http://new-host:8086",
        }

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_reconfigure(user_input=updated_input)

        assert result["type"] == "abort"
        assert result["reason"] == "reconfigure_successful"
        mock_client.async_validate_connection.assert_awaited_once()
        mock_client.async_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconfigure_connection_error(self):
        entry = _mock_config_entry()
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=entry)
        flow.context = {"entry_id": entry.entry_id}

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock(
            side_effect=SolectrusInfluxError("Timeout"),
        )
        mock_client.async_close = AsyncMock()

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_reconfigure(
                user_input=dict(VALID_USER_INPUT),
            )

        assert result["type"] == "form"
        assert result["errors"] == {"base": "cannot_connect"}
        assert result["description_placeholders"]["error"] == "Timeout"
        mock_client.async_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconfigure_unexpected_error(self):
        entry = _mock_config_entry()
        flow = SolectrusConfigFlow()
        flow.hass = _mock_hass(entry=entry)
        flow.context = {"entry_id": entry.entry_id}

        mock_client = MagicMock()
        mock_client.async_validate_connection = AsyncMock(
            side_effect=ValueError("bad config"),
        )
        mock_client.async_close = AsyncMock()

        with patch(
            "custom_components.solectrus.config_flow.SolectrusInfluxClient",
            return_value=mock_client,
        ):
            result = await flow.async_step_reconfigure(
                user_input=dict(VALID_USER_INPUT),
            )

        assert result["type"] == "form"
        assert result["errors"] == {"base": "unknown"}
        mock_client.async_close.assert_awaited_once()


class TestOptionsFlowInit:
    """Tests for SolectrusOptionsFlowHandler.async_step_init."""

    @pytest.mark.asyncio
    async def test_options_init_shows_form(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()

        result = await handler.async_step_init(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"

    @pytest.mark.asyncio
    async def test_options_init_with_existing_advanced(self):
        entry = _mock_config_entry(options={"advanced": True})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()

        result = await handler.async_step_init(user_input=None)

        assert result["type"] == "form"
        # The schema should have advanced defaulting to True
        for key in result["data_schema"].schema:
            if str(key) == "advanced" and hasattr(key, "default"):
                assert key.default() is True

    @pytest.mark.asyncio
    async def test_options_init_advances_to_sensors(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()

        result = await handler.async_step_init(user_input={"advanced": True})

        # Should advance to the sensors step form
        assert result["type"] == "form"
        assert result["step_id"] == "sensors"
        assert handler._show_advanced is True

    @pytest.mark.asyncio
    async def test_options_init_basic_mode(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()

        result = await handler.async_step_init(user_input={"advanced": False})

        assert result["type"] == "form"
        assert result["step_id"] == "sensors"
        assert handler._show_advanced is False


class TestOptionsFlowSensors:
    """Tests for SolectrusOptionsFlowHandler.async_step_sensors."""

    @pytest.mark.asyncio
    async def test_sensors_form_shown(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()
        handler._show_advanced = False

        result = await handler.async_step_sensors(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "sensors"

    @pytest.mark.asyncio
    async def test_sensors_create_entry_basic(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()
        handler._show_advanced = False

        user_input = {
            "INVERTER_POWER_entity": "sensor.inverter_power",
            "HOUSE_POWER_entity": "sensor.house_power",
        }
        result = await handler.async_step_sensors(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"]["advanced"] is False
        sensors = result["data"][CONF_SENSORS]
        assert "INVERTER_POWER" in sensors
        assert "HOUSE_POWER" in sensors
        assert sensors["INVERTER_POWER"][CONF_ENTITY_ID] == "sensor.inverter_power"

    @pytest.mark.asyncio
    async def test_sensors_create_entry_advanced(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()
        handler._show_advanced = True

        user_input = {
            "INVERTER_POWER_entity": "sensor.inverter_power",
            "INVERTER_POWER_measurement": "custom_meas",
            "INVERTER_POWER_field": "custom_field",
        }
        result = await handler.async_step_sensors(user_input=user_input)

        assert result["type"] == "create_entry"
        assert result["data"]["advanced"] is True
        sensor = result["data"][CONF_SENSORS]["INVERTER_POWER"]
        assert sensor[CONF_MEASUREMENT] == "custom_meas"
        assert sensor[CONF_FIELD] == "custom_field"

    @pytest.mark.asyncio
    async def test_sensors_empty_input_creates_empty_sensors(self):
        entry = _mock_config_entry(options={})
        handler = SolectrusOptionsFlowHandler(entry)
        handler.hass = _mock_hass()
        handler._show_advanced = False

        result = await handler.async_step_sensors(user_input={})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_SENSORS] == {}


class TestOptionsFlowFactory:
    """Tests for async_get_options_flow factory method."""

    def test_returns_options_handler(self):
        entry = _mock_config_entry()
        handler = SolectrusConfigFlow.async_get_options_flow(entry)

        assert isinstance(handler, SolectrusOptionsFlowHandler)
        assert handler._config_entry is entry
