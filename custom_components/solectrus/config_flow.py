"""Config flow for the SOLECTRUS integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .api import SolectrusInfluxClient, SolectrusInfluxError
from .const import (
    CAR_COUNT,
    CAR_SENSOR_NUMBERS,
    CONF_BUCKET,
    CONF_ENTITY_ID,
    CONF_FIELD,
    CONF_MEASUREMENT,
    CONF_ORG,
    CONF_SENSORS,
    CONF_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    SENSOR_DEFINITIONS,
    SensorDefinition,
)


class SolectrusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SOLECTRUS."""

    VERSION = 1
    reconfigure_supported = True

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            client = SolectrusInfluxClient(
                url=user_input[CONF_URL],
                token=user_input[CONF_TOKEN],
                org=user_input[CONF_ORG],
                bucket=user_input[CONF_BUCKET],
                verify_ssl=user_input.get(CONF_VERIFY_SSL, True),
            )
            try:
                await client.async_validate_connection()
            except SolectrusInfluxError as exc:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(exc)
                LOGGER.warning(
                    "Influx validation failed (%s): %s", type(exc).__name__, exc
                )
            except Exception as exc:  # noqa: BLE001
                errors["base"] = "unknown"
                LOGGER.exception("Unexpected error during Influx validation: %s", exc)
            else:
                # The entry's update listener (see __init__.py) schedules the
                # reload, so update the entry and abort without an extra reload.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **user_input}
                )
                return self.async_abort(reason="reconfigure_successful")
            finally:
                await client.async_close()

        defaults = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_influx_schema(defaults),
            errors=errors,
            description_placeholders=placeholders or None,
        )

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            client = SolectrusInfluxClient(
                url=user_input[CONF_URL],
                token=user_input[CONF_TOKEN],
                org=user_input[CONF_ORG],
                bucket=user_input[CONF_BUCKET],
                verify_ssl=user_input.get(CONF_VERIFY_SSL, True),
            )
            try:
                await client.async_validate_connection()
            except SolectrusInfluxError as exc:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(exc)
                # Keep log terse; full message in warning for diagnostics.
                LOGGER.warning(
                    "Influx validation failed (%s): %s", type(exc).__name__, exc
                )
            except Exception as exc:  # noqa: BLE001
                errors["base"] = "unknown"
                LOGGER.exception("Unexpected error during Influx validation: %s", exc)
            else:
                unique_id = f"{user_input[CONF_URL]}::{user_input[CONF_BUCKET]}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="InfluxDB-Exporter",
                    data=user_input,
                )
            finally:
                await client.async_close()

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=_influx_schema(defaults),
            errors=errors,
            description_placeholders=placeholders or None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler."""
        return SolectrusOptionsFlowHandler(config_entry)


class SolectrusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for sensor/entity mapping."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._show_advanced: bool = bool(config_entry.options.get("advanced", False))

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Start options flow."""
        if user_input is not None:
            self._show_advanced = bool(user_input.get("advanced", False))
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "advanced",
                        default=bool(self._config_entry.options.get("advanced", False)),
                    ): selector.BooleanSelector(selector.BooleanSelectorConfig())
                }
            ),
        )

    async def async_step_sensors(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the sensor mapping options."""
        if user_input is not None:
            existing_sensors: dict = self._config_entry.options.get(CONF_SENSORS, {})
            sensors = _parse_sensors_input(
                user_input,
                existing_sensors,
                show_advanced=self._show_advanced,
            )

            return self.async_create_entry(
                title=self._config_entry.title,
                data={"advanced": self._show_advanced, CONF_SENSORS: sensors},
            )

        options_sensors: dict = self._config_entry.options.get(CONF_SENSORS, {})
        schema_dict = _build_sensors_schema(
            options_sensors, show_advanced=self._show_advanced
        )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(schema_dict),
        )


def _influx_schema(defaults: dict) -> vol.Schema:
    """Build schema for InfluxDB connection fields."""
    return vol.Schema(
        {
            vol.Required(
                CONF_URL,
                default=defaults.get(CONF_URL, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.URL,
                ),
            ),
            vol.Required(
                CONF_TOKEN,
                default=defaults.get(CONF_TOKEN, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                ),
            ),
            vol.Required(
                CONF_ORG,
                default=defaults.get(CONF_ORG, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                ),
            ),
            vol.Required(
                CONF_BUCKET,
                default=defaults.get(CONF_BUCKET, vol.UNDEFINED),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                ),
            ),
            vol.Optional(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, True),
            ): selector.BooleanSelector(selector.BooleanSelectorConfig()),
        }
    )


def _build_sensors_schema(existing_sensors: dict, *, show_advanced: bool) -> dict:
    schema_dict: dict = {}
    mapped_cars: set[int] = set()

    for key, definition in SENSOR_DEFINITIONS.items():
        configured = existing_sensors.get(key, {})
        fields = _sensor_fields(
            key, definition, configured, show_advanced=show_advanced
        )
        number = CAR_SENSOR_NUMBERS.get(key)
        if number is None:
            schema_dict.update(fields)
            continue
        # Collect the fields of each car where its first sensor is.
        schema_dict.setdefault(vol.Required(f"car_{number}"), {}).update(fields)
        if configured.get(CONF_ENTITY_ID):
            mapped_cars.add(number)

    # Turn the collected fields into sections, collapsed while no sensor of
    # the car is mapped.
    for number in range(1, CAR_COUNT + 1):
        key = f"car_{number}"
        schema_dict[key] = section(
            vol.Schema(schema_dict[key]), {"collapsed": number not in mapped_cars}
        )
    return schema_dict


def _sensor_fields(
    key: str,
    definition: SensorDefinition,
    configured: dict,
    *,
    show_advanced: bool,
) -> dict:
    fields: dict = {
        vol.Optional(
            f"{key}_entity",
            description={
                "suggested_value": configured.get(CONF_ENTITY_ID),
            },
        ): selector.EntitySelector(selector.EntitySelectorConfig())
    }
    if show_advanced:
        fields[
            vol.Optional(
                f"{key}_measurement",
                default=configured.get(CONF_MEASUREMENT, definition.measurement),
            )
        ] = selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            )
        )
        fields[
            vol.Optional(
                f"{key}_field",
                default=configured.get(CONF_FIELD, definition.field),
            )
        ] = selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT,
            )
        )
    return fields


def _parse_sensors_input(
    user_input: dict, existing_sensors: dict, *, show_advanced: bool
) -> dict[str, dict[str, str]]:
    # Lift the fields of the car sections to the top level.
    flat_input: dict = {}
    for name, value in user_input.items():
        if isinstance(value, dict):
            flat_input.update(value)
        else:
            flat_input[name] = value

    sensors: dict[str, dict[str, str]] = {}
    for key, definition in SENSOR_DEFINITIONS.items():
        configured = existing_sensors.get(key, {})
        entity_id = flat_input.get(f"{key}_entity")
        measurement = configured.get(CONF_MEASUREMENT, definition.measurement)
        field = configured.get(CONF_FIELD, definition.field)
        if show_advanced:
            measurement = flat_input.get(f"{key}_measurement") or measurement
            field = flat_input.get(f"{key}_field") or field
        if entity_id:
            sensors[key] = {
                CONF_ENTITY_ID: entity_id,
                CONF_MEASUREMENT: measurement,
                CONF_FIELD: field,
            }
    return sensors
