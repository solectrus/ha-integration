# SOLECTRUS Home Assistant Integration

**Warning:** This repository is experimental and unsupported; do not use it in production.

This custom integration forwards Home Assistant entity values into an InfluxDB bucket used by your SOLECTRUS instance. It is tailored for the SOLECTRUS sensor keys so you can map each one to a Home Assistant entity, optionally overriding measurement and field names.

## Features

- Configure InfluxDB URL, token, organisation, and bucket directly in the config flow.
- Map every SOLECTRUS sensor to a Home Assistant entity via the options flow; measurement/field defaults are pre-filled but can be overridden.
- Writes are throttled to no more than once every 5 seconds per sensor and at least once every 5 minutes (last known value), skipping repeated zero values.

## Requirements

- Home Assistant `2025.9.4` or newer
- InfluxDB 2.x reachable from Home Assistant (URL + org + bucket + token)
- An InfluxDB token that can:
  - read bucket metadata (bucket lookup during setup)
  - write data into the target bucket

## Installation

### HACS

1. HACS → **Integrations** → **⋮** → **Custom repositories**
2. Add `https://github.com/solectrus/ha-integration` as type **Integration**
3. Install **SOLECTRUS**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/solectrus_integration` into your Home Assistant `config/custom_components/` folder
2. Restart Home Assistant

## Setup (in Home Assistant)

1. Ensure the integration is installed (see **Installation** above).
2. Go to **Settings → Devices & services → Add integration** and search for **SOLECTRUS**.
3. Enter your InfluxDB connection details (URL, token, org, bucket). The integration validates access by looking up the bucket.
4. Open the integration **Options** and map the SOLECTRUS sensor keys to the Home Assistant entities you want to forward.

Notes:

- This integration does not create entities; it exports values of existing entities you select in the options flow.
- If you don't configure any mappings, no data will be written.

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

## Troubleshooting

- **Setup error “Bucket not found or token lacks permission to read it”**: ensure the bucket exists and the token has permission to read bucket metadata (not only write).
- **TLS/certificate errors**: `https://` connections verify certificates; use a valid cert/CA or use `http://` for local, non-TLS InfluxDB.
- **`field type conflict` in InfluxDB**: set the matching **Data type** in **Advanced options** to the field's existing type.
