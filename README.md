# SOLECTRUS Home Assistant Integration

**Warning:** This repository is experimental and unsupported; do not use it in production.

This custom integration forwards Home Assistant entity values to the external SOLECTRUS instance. It is tailored for the SOLECTRUS sensors so you can map each one to an entity, optionally overriding measurement and field names.

## Features

- Configure InfluxDB URL, token, organisation, and bucket directly in the config flow.
- Map every SOLECTRUS sensor to a Home Assistant entity via the options flow; measurement/field defaults are pre-filled but can be overridden.
- Writes are throttled to no more than once every 5 seconds per sensor and at least once every 5 minutes (last known value), skipping repeated zero values.

## Setup

1. Install the integration (e.g. via HACS or as a custom component in `custom_components/solectrus_integration`).
2. Add the integration in Home Assistant and enter your InfluxDB connection details.
3. Open the integration options to map the provided SOLECTRUS sensor keys to the Home Assistant entities you want to forward. Leave measurement/field empty to use defaults.

### Advanced options

In the options flow you can enable **Advanced options**. This shows additional fields per sensor:

- **Measurement**: override the default measurement name.
- **Field**: override the default field name.
- **Data type**: enforce the value type that is written to InfluxDB.

The data type is important because InfluxDB does not allow changing a field type after it exists. If your bucket already contains a field as `integer`, the integration must keep writing integers, otherwise you will see `field type conflict` errors.

Supported data types:

- `int` – writes integer values (default for power/Watt sensors).
- `float` – writes floating point values (default for temperatures and SOC).
- `bool` – writes boolean values.
- `string` – writes strings (default for `SYSTEM_STATUS`).

If the incoming Home Assistant state cannot be converted to the selected type, it is skipped.

Once configured, the integration listens for entity state changes and writes them to InfluxDB following the above rules.
