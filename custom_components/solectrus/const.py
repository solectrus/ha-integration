"""Constants and sensor metadata for the SOLECTRUS integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from logging import Logger, getLogger
from typing import Final

LOGGER: Logger = getLogger(__package__)

DOMAIN = "solectrus"

FORECAST_SENSOR_KEYS: Final[set[str]] = {
    "INVERTER_POWER_FORECAST_CLEARSKY",
    "INVERTER_POWER_FORECAST",
    "OUTDOOR_TEMP_FORECAST",
}

# Candidate (key, multiplier) pairs to look up inside each item of a
# "forecast" attribute list, tried in order; the first key present in an item
# wins and its value is multiplied by the paired factor. The field name alone
# ("power") matches simple/generic forecast sources, while the extra names
# cover integrations that name (and scale) their per-metric forecast keys
# differently:
# - pvnode: "watts" / "watts_clearsky", already in W.
# - ha-solcast-solar: "pv_estimate" in the detailedForecast/detailedHourly
#   attribute, reported in kW, hence the x1000 factor to match the Watt-based
#   INVERTER_POWER_FORECAST field. Solcast has no clear-sky or temperature
#   forecast, so it only applies to INVERTER_POWER_FORECAST.
FORECAST_ATTRIBUTE_VALUE_KEYS: Final[dict[str, tuple[tuple[str, float], ...]]] = {
    "INVERTER_POWER_FORECAST": (("power", 1), ("watts", 1), ("pv_estimate", 1000)),
    "INVERTER_POWER_FORECAST_CLEARSKY": (("power", 1), ("watts_clearsky", 1)),
    "OUTDOOR_TEMP_FORECAST": (("temperature", 1),),
}

# Candidate state attribute names holding the forecast time series list,
# tried in order; the first one present (as a non-empty list) wins.
# "forecast" covers pvnode and generic sources. ha-solcast-solar instead uses
# "detailedForecast" (finer-grained, preferred when present) or
# "detailedHourly" on its energy forecast sensors.
FORECAST_ATTRIBUTE_NAMES: Final[tuple[str, ...]] = (
    "forecast",
    "detailedForecast",
    "detailedHourly",
)

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"  # noqa: S105
CONF_ORG: Final = "org"
CONF_BUCKET: Final = "bucket"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SENSORS: Final = "sensors"
CONF_ENTITY_ID: Final = "entity_id"
CONF_MEASUREMENT: Final = "measurement"
CONF_FIELD: Final = "field"
DATA_TYPE_INT: Final = "int"
DATA_TYPE_FLOAT: Final = "float"
DATA_TYPE_BOOL: Final = "bool"
DATA_TYPE_STRING: Final = "string"


@dataclass(frozen=True)
class SensorDefinition:
    """
    Default mapping and fallback datatype for a SOLECTRUS sensor.

    The fallback type is only applied when InfluxDB has no prior data for
    the (measurement, field) pair; otherwise the type detected from existing
    data wins, since Influx freezes the type once a field is written.

    If the state of the entity has the attribute, its value is used instead
    of the state (for example the latitude of a device_tracker).
    """

    measurement: str
    field: str
    data_type: str
    min_value: float | None = None
    max_value: float | None = None
    attribute: str | None = None


SENSOR_DEFINITIONS: dict[str, SensorDefinition] = {
    "INVERTER_POWER": SensorDefinition("inverter", "power", DATA_TYPE_INT, min_value=0),
    "INVERTER_POWER_1": SensorDefinition(
        "inverter_1", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_2": SensorDefinition(
        "inverter_2", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_3": SensorDefinition(
        "inverter_3", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_4": SensorDefinition(
        "inverter_4", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_5": SensorDefinition(
        "inverter_5", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_FORECAST": SensorDefinition(
        "inverter_forecast", "power", DATA_TYPE_INT, min_value=0
    ),
    "INVERTER_POWER_FORECAST_CLEARSKY": SensorDefinition(
        "inverter_forecast_clearsky", "power", DATA_TYPE_INT, min_value=0
    ),
    "HOUSE_POWER": SensorDefinition("house", "power", DATA_TYPE_INT, min_value=0),
    "BATTERY_SOC": SensorDefinition(
        "battery", "soc", DATA_TYPE_FLOAT, min_value=0, max_value=100
    ),
    "BATTERY_CHARGING_POWER": SensorDefinition(
        "battery", "charging_power", DATA_TYPE_INT, min_value=0
    ),
    "BATTERY_DISCHARGING_POWER": SensorDefinition(
        "battery", "discharging_power", DATA_TYPE_INT, min_value=0
    ),
    "HEATPUMP_POWER": SensorDefinition("heatpump", "power", DATA_TYPE_INT, min_value=0),
    "HEATPUMP_HEATING_POWER": SensorDefinition(
        "heatpump", "heating_power", DATA_TYPE_INT, min_value=0
    ),
    "HEATPUMP_TANK_TEMP": SensorDefinition("heatpump", "tank_temp", DATA_TYPE_FLOAT),
    "HEATPUMP_TANK_TEMP_SETPOINT": SensorDefinition(
        "heatpump", "tank_temp_setpoint", DATA_TYPE_FLOAT
    ),
    "HEATPUMP_STATUS": SensorDefinition("heatpump", "status", DATA_TYPE_STRING),
    "OUTDOOR_TEMP_FORECAST": SensorDefinition(
        "outdoor_forecast", "temperature", DATA_TYPE_FLOAT
    ),
    "GRID_EXPORT_POWER": SensorDefinition(
        "grid", "export_power", DATA_TYPE_INT, min_value=0
    ),
    "GRID_EXPORT_LIMIT": SensorDefinition(
        "grid", "export_limit", DATA_TYPE_INT, min_value=0
    ),
    "GRID_IMPORT_POWER": SensorDefinition(
        "grid", "import_power", DATA_TYPE_INT, min_value=0
    ),
    "WALLBOX_POWER": SensorDefinition("wallbox", "power", DATA_TYPE_INT, min_value=0),
    "WALLBOX_CONNECTED": SensorDefinition("wallbox", "connected", DATA_TYPE_BOOL),
    "CASE_TEMP": SensorDefinition("case", "temperature", DATA_TYPE_FLOAT),
    "OUTDOOR_TEMP": SensorDefinition("outdoor", "temperature", DATA_TYPE_FLOAT),
    "SYSTEM_STATUS": SensorDefinition("system", "status", DATA_TYPE_STRING),
    "SYSTEM_STATUS_OK": SensorDefinition("system", "status_ok", DATA_TYPE_BOOL),
}

# SOLECTRUS supports up to five electric cars. Car 1 has the keys without a
# number and the measurement "car". Cars 2-5 add the number to both
# (CAR_ODOMETER_2, "car_2").
CAR_COUNT: Final = 5
CAR_SENSOR_DEFINITIONS: Final[dict[str, SensorDefinition]] = {
    "CAR_BATTERY_SOC": SensorDefinition(
        "car", "battery_soc", DATA_TYPE_FLOAT, min_value=0, max_value=100
    ),
    "CAR_ODOMETER": SensorDefinition("car", "odometer", DATA_TYPE_FLOAT, min_value=0),
    "CAR_RANGE": SensorDefinition("car", "range", DATA_TYPE_FLOAT, min_value=0),
    "CAR_CONNECTED": SensorDefinition("car", "connected", DATA_TYPE_BOOL),
    "CAR_LATITUDE": SensorDefinition(
        "car",
        "latitude",
        DATA_TYPE_FLOAT,
        min_value=-90,
        max_value=90,
        attribute="latitude",
    ),
    "CAR_LONGITUDE": SensorDefinition(
        "car",
        "longitude",
        DATA_TYPE_FLOAT,
        min_value=-180,
        max_value=180,
        attribute="longitude",
    ),
}

# Sensor key -> number of its car
CAR_SENSOR_NUMBERS: Final[dict[str, int]] = {}

for number in range(1, CAR_COUNT + 1):
    suffix = "" if number == 1 else f"_{number}"
    for prefix, definition in CAR_SENSOR_DEFINITIONS.items():
        SENSOR_DEFINITIONS[prefix + suffix] = replace(
            definition, measurement=f"car{suffix}"
        )
        CAR_SENSOR_NUMBERS[prefix + suffix] = number

for index in range(1, 21):
    key = f"CUSTOM_{index:02d}"
    SENSOR_DEFINITIONS[key] = SensorDefinition(
        f"custom_{index:02d}", "power", DATA_TYPE_INT, min_value=0
    )
