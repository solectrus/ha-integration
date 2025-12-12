"""Config flow for the SOLECTRUS integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import SolectrusInfluxClient, SolectrusInfluxError
from .const import (
    CONF_BUCKET,
    CONF_DATA_TYPE,
    CONF_ENTITY_ID,
    CONF_FIELD,
    CONF_MEASUREMENT,
    CONF_ORG,
    CONF_SENSORS,
    CONF_TOKEN,
    CONF_URL,
    DATA_TYPE_OPTIONS,
    DOMAIN,
    LOGGER,
    SENSOR_DEFINITIONS,
)


class SolectrusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SOLECTRUS."""

    VERSION = 1

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
                await client.async_close()
                return self.async_create_entry(
                    title="SOLECTRUS",
                    data=user_input,
                )
            finally:
                await client.async_close()

        defaults = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
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
                },
            ),
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
        """Decide whether to show advanced fields."""
        if user_input is not None:
            self._show_advanced = user_input.get("advanced", False)
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "advanced",
                        default=self._show_advanced,
                    ): selector.BooleanSelector()
                }
            ),
        )

    async def async_step_sensors(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the sensor mapping options."""
        if user_input is not None:
            sensors: dict[str, dict[str, str]] = {}
            existing_sensors: dict = self._config_entry.options.get(CONF_SENSORS, {})
            for key, definition in SENSOR_DEFINITIONS.items():
                configured = existing_sensors.get(key, {})
                entity_id = user_input.get(f"{key}_entity")
                measurement = configured.get(CONF_MEASUREMENT, definition.measurement)
                field = configured.get(CONF_FIELD, definition.field)
                data_type = configured.get(CONF_DATA_TYPE, definition.data_type)
                if self._show_advanced:
                    measurement = user_input.get(f"{key}_measurement") or measurement
                    field = user_input.get(f"{key}_field") or field
                    data_type = user_input.get(f"{key}_data_type") or data_type
                if entity_id:
                    sensors[key] = {
                        CONF_ENTITY_ID: entity_id,
                        CONF_MEASUREMENT: measurement,
                        CONF_FIELD: field,
                        CONF_DATA_TYPE: data_type,
                    }

            return self.async_create_entry(
                title=self._config_entry.title,
                data={"advanced": self._show_advanced, CONF_SENSORS: sensors},
            )

        options_sensors: dict = self._config_entry.options.get(CONF_SENSORS, {})
        schema_dict: dict = {}
        for key, definition in SENSOR_DEFINITIONS.items():
            configured = options_sensors.get(key, {})
            schema_dict[
                vol.Optional(
                    f"{key}_entity",
                    default=configured.get(CONF_ENTITY_ID, vol.UNDEFINED),
                )
            ] = vol.Any(
                None,
                selector.EntitySelector(selector.EntitySelectorConfig()),
            )
            if self._show_advanced:
                schema_dict[
                    vol.Optional(
                        f"{key}_measurement",
                        default=configured.get(
                            CONF_MEASUREMENT, definition.measurement
                        ),
                    )
                ] = selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                )
                schema_dict[
                    vol.Optional(
                        f"{key}_field",
                        default=configured.get(CONF_FIELD, definition.field),
                    )
                ] = selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                )
                schema_dict[
                    vol.Optional(
                        f"{key}_data_type",
                        default=configured.get(CONF_DATA_TYPE, definition.data_type),
                    )
                ] = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DATA_TYPE_OPTIONS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(schema_dict),
        )
