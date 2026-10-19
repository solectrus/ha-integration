"""Config flow for the SOLECTRUS integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .api import SolectrusInfluxClient, SolectrusInfluxError
from .const import (
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
    SENSOR_GROUPS,
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


# The groups that are open at the first setup, when no sensor is mapped yet
FIRST_SETUP_GROUPS = ("inverter", "grid")


def _build_sensors_schema(existing_sensors: dict, *, show_advanced: bool) -> dict:
    def mapped(key: str) -> bool:
        return bool(existing_sensors.get(key, {}).get(CONF_ENTITY_ID))

    first_setup = not any(mapped(key) for key in SENSOR_DEFINITIONS)
    schema_dict: dict = {}

    # Each group is a section, collapsed while none of its sensors is mapped.
    for group, definitions in SENSOR_GROUPS.items():
        fields: dict = {}
        for key, definition in definitions.items():
            fields.update(
                _sensor_fields(
                    key,
                    definition,
                    existing_sensors.get(key, {}),
                    show_advanced=show_advanced,
                )
            )
        expanded = any(mapped(key) for key in definitions) or (
            first_setup and group in FIRST_SETUP_GROUPS
        )
        schema_dict[vol.Required(group)] = section(
            vol.Schema(fields), {"collapsed": not expanded}
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
        text = selector.TextSelector()
        fields[
            vol.Optional(
                f"{key}_measurement",
                default=configured.get(CONF_MEASUREMENT, definition.measurement),
            )
        ] = text
        fields[
            vol.Optional(
                f"{key}_field",
                default=configured.get(CONF_FIELD, definition.field),
            )
        ] = text
    return fields


def _parse_sensors_input(
    user_input: dict, existing_sensors: dict, *, show_advanced: bool
) -> dict[str, dict[str, str]]:
    # The form sends the fields inside the section of each group.
    flat_input = {
        name: value for fields in user_input.values() for name, value in fields.items()
    }

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
