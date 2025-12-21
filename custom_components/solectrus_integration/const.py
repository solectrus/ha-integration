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
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SENSORS: Final = "sensors"
CONF_ENTITY_ID: Final = "entity_id"
CONF_MEASUREMENT: Final = "measurement"
CONF_FIELD: Final = "field"
CONF_DATA_TYPE: Final = "data_type"
DATA_TYPE_INT: Final = "int"
DATA_TYPE_FLOAT: Final = "float"
DATA_TYPE_BOOL: Final = "bool"
DATA_TYPE_STRING: Final = "string"

DATA_TYPE_OPTIONS: Final = [
    DATA_TYPE_INT,
    DATA_TYPE_FLOAT,
    DATA_TYPE_BOOL,
    DATA_TYPE_STRING,
]


@dataclass(frozen=True)
class SensorDefinition:
    """Default mapping and datatype for a SOLECTRUS sensor."""

    measurement: str
    field: str
    data_type: str


SENSOR_DEFINITIONS: dict[str, SensorDefinition] = {
    "INVERTER_POWER": SensorDefinition("inverter", "power", DATA_TYPE_INT),
    "INVERTER_POWER_1": SensorDefinition("inverter_1", "power", DATA_TYPE_INT),
    "INVERTER_POWER_2": SensorDefinition("inverter_2", "power", DATA_TYPE_INT),
    "INVERTER_POWER_3": SensorDefinition("inverter_3", "power", DATA_TYPE_INT),
    "INVERTER_POWER_4": SensorDefinition("inverter_4", "power", DATA_TYPE_INT),
    "INVERTER_POWER_5": SensorDefinition("inverter_5", "power", DATA_TYPE_INT),
    "INVERTER_POWER_FORECAST": SensorDefinition(
        "inverter_forecast", "power", DATA_TYPE_INT
    ),
    "HOUSE_POWER": SensorDefinition("house", "power", DATA_TYPE_INT),
    "BATTERY_SOC": SensorDefinition("battery", "soc", DATA_TYPE_FLOAT),
    "BATTERY_CHARGING_POWER": SensorDefinition(
        "battery", "charging_power", DATA_TYPE_INT
    ),
    "BATTERY_DISCHARGING_POWER": SensorDefinition(
        "battery", "discharging_power", DATA_TYPE_INT
    ),
    "HEATPUMP_POWER": SensorDefinition("heatpump", "power", DATA_TYPE_INT),
    "HEATPUMP_TANK_TEMP": SensorDefinition("heatpump", "tank_temp", DATA_TYPE_FLOAT),
    "HEATPUMP_STATUS": SensorDefinition("heatpump", "status", DATA_TYPE_STRING),
    "OUTDOOR_TEMP_FORECAST": SensorDefinition(
        "outdoor_forecast", "temperature", DATA_TYPE_FLOAT
    ),
    "GRID_POWER_EXPORT": SensorDefinition("grid", "export_power", DATA_TYPE_INT),
    "GRID_EXPORT_LIMIT": SensorDefinition("grid", "export_limit", DATA_TYPE_INT),
    "GRID_POWER_IMPORT": SensorDefinition("grid", "import_power", DATA_TYPE_INT),
    "WALLBOX_POWER": SensorDefinition("wallbox", "power", DATA_TYPE_INT),
    "WALLBOX_CONNECTED": SensorDefinition("wallbox", "connected", DATA_TYPE_BOOL),
    "CASE_TEMP": SensorDefinition("case", "temperature", DATA_TYPE_FLOAT),
    "CAR_BATTERY_SOC": SensorDefinition("car", "battery_soc", DATA_TYPE_FLOAT),
    "CAR_MILEAGE": SensorDefinition("car", "mileage", DATA_TYPE_INT),
    "OUTDOOR_TEMP": SensorDefinition("outdoor", "temperature", DATA_TYPE_FLOAT),
    "SYSTEM_STATUS": SensorDefinition("system", "status", DATA_TYPE_STRING),
    "SYSTEM_STATUS_OK": SensorDefinition("system", "status_ok", DATA_TYPE_BOOL),
}

for index in range(1, 21):
    key = f"CUSTOM_{index:02d}"
    SENSOR_DEFINITIONS[key] = SensorDefinition(
        f"custom_{index:02d}", "power", DATA_TYPE_INT
    )
