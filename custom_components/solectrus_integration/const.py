"""Constants and sensor metadata for the SOLECTRUS integration."""

from __future__ import annotations

from dataclasses import dataclass
from logging import Logger, getLogger
from typing import Final

LOGGER: Logger = getLogger(__package__)

DOMAIN = "solectrus_integration"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"  # noqa: S105
CONF_ORG: Final = "org"
CONF_BUCKET: Final = "bucket"
CONF_SENSORS: Final = "sensors"
CONF_ENTITY_ID: Final = "entity_id"
CONF_MEASUREMENT: Final = "measurement"
CONF_FIELD: Final = "field"


@dataclass(frozen=True)
class SensorDefinition:
    """Default measurement/field for a SOLECTRUS sensor."""

    measurement: str
    field: str


SENSOR_DEFINITIONS: dict[str, SensorDefinition] = {
    "INVERTER_POWER": SensorDefinition("inverter", "power"),
    "INVERTER_POWER_1": SensorDefinition("inverter_1", "power"),
    "INVERTER_POWER_2": SensorDefinition("inverter_2", "power"),
    "INVERTER_POWER_3": SensorDefinition("inverter_3", "power"),
    "INVERTER_POWER_4": SensorDefinition("inverter_4", "power"),
    "INVERTER_POWER_5": SensorDefinition("inverter_5", "power"),
    "HOUSE_POWER": SensorDefinition("house", "power"),
    "BATTERY_SOC": SensorDefinition("battery", "soc"),
    "BATTERY_CHARGING_POWER": SensorDefinition("battery", "charging_power"),
    "BATTERY_DISCHARGING_POWER": SensorDefinition("battery", "discharging_power"),
    "HEATPUMP_POWER": SensorDefinition("heatpump", "power"),
    "HEATPUMP_TANK_TEMP": SensorDefinition("heatpump", "tank_temp"),
    "OUTDOOR_TEMP_FORECAST": SensorDefinition("outdoor_forecast", "temperature"),
    "GRID_POWER_EXPORT": SensorDefinition("grid", "export_power"),
    "GRID_POWER_IMPORT": SensorDefinition("grid", "import_power"),
    "WALLBOX_POWER": SensorDefinition("wallbox", "power"),
    "CASE_TEMP": SensorDefinition("case", "temperature"),
    "CAR_BATTERY_SOC": SensorDefinition("car", "battery_soc"),
    "CAR_MILEAGE": SensorDefinition("car", "mileage"),
    "OUTDOOR_TEMP": SensorDefinition("outdoor", "temperature"),
    "SYSTEM_STATUS": SensorDefinition("system", "status"),
}

for index in range(1, 21):
    key = f"CUSTOM_{index:02d}"
    SENSOR_DEFINITIONS[key] = SensorDefinition(f"custom_{index:02d}", "power")
