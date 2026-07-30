# SOLECTRUS Home Assistant Integration

**Note:** This integration is in an early stage of development. Please report any issues you encounter.

This custom integration forwards Home Assistant entity values into an InfluxDB bucket used by your SOLECTRUS instance. It is tailored for the SOLECTRUS sensor keys so you can map each one to a Home Assistant entity, optionally overriding measurement and field names.

## Features

- Configure InfluxDB URL, token, organisation, and bucket directly in the config flow.
- Map every SOLECTRUS sensor to a Home Assistant entity via the options flow; measurement/field defaults are pre-filled but can be overridden.
- Writes are batched and sent every 5 seconds; points are deduplicated by `(sensor, timestamp)` (value may repeat).
- When a sensor stayed at `0` for a longer time and then resumes with a positive value, the integration inserts an extra `0` point 1 second before the resume to avoid interpolation ramps.
- Forecast sensors (power, clear-sky power, outdoor temperature) are batched as full time series instead of a single point - see [Forecast sensors](#forecast-sensors).

## Requirements

- Home Assistant `2024.6` or newer
- InfluxDB 2.x reachable from Home Assistant (URL + org + bucket + token)
- An InfluxDB token with **read and write** access to the target bucket (read is used to detect existing field types so the integration matches them automatically)

### InfluxDB URL

You can enter the URL with or without a scheme:

- `192.168.1.10:8086` or `influxdb.local:8086` → treated as **`http://`** (InfluxDB usually runs on the same internal/local network as Home Assistant).
- `https://influxdb.example.com` → used as-is; toggle **Verify SSL certificate** in the config flow to control certificate validation for that connection.

## Installation

### HACS

1. HACS → **Integrations** → **⋮** → **Custom repositories**
2. Add `https://github.com/solectrus/ha-integration` as type **Integration**
3. Install **SOLECTRUS**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/solectrus` into your Home Assistant `config/custom_components/` folder
2. Restart Home Assistant

## Setup (in Home Assistant)

1. Ensure the integration is installed (see **Installation** above).
2. Go to **Settings → Devices & services → Add integration** and search for **SOLECTRUS**.
3. Enter your InfluxDB connection details (URL, token, org, bucket). The integration validates access by writing a test point.
4. Open the integration **Options** and map the SOLECTRUS sensor keys to the Home Assistant entities you want to forward.

Notes:

- This integration does not create entities; it exports values of existing entities you select in the options flow.
- If you don't configure any mappings, no data will be written.

### Data type detection

The integration determines the InfluxDB field type for each (measurement, field) pair automatically on startup:

1. If the bucket already contains data for the field, the existing type wins (Influx freezes the field type on first write, so we have to match it).
2. Otherwise, a curated default per sensor is used (`int` for power, `float` for SOC/temperatures, `bool` for connection states, `string` for status).

If the incoming Home Assistant state cannot be converted to the resolved type, it is skipped.

### Advanced options

In the options flow you can enable **Advanced options**. This shows additional fields per sensor:

- **Measurement**: override the default measurement name.
- **Field**: override the default field name.

Once configured, the integration listens for entity state changes and writes them to InfluxDB following the above rules.

### Forecast sensors

Three sensor keys are treated as forecast time series instead of a single current value:

- `INVERTER_POWER_FORECAST`
- `INVERTER_POWER_FORECAST_CLEARSKY`
- `OUTDOOR_TEMP_FORECAST`

Instead of writing just the entity's current state, the integration reads the entity's forecast time series attribute (if present) and writes every entry as its own point, timestamped accordingly. This is picked up automatically - no extra configuration needed - as long as the mapped entity exposes one of the following:

- **`forecast` attribute** (a list of `{datetime/time/period_end, ...}` entries) - this is what [pvnode](https://github.com/patricknitsch/ha-pvnode) exposes on its power (`watts`), clear-sky (`watts_clearsky`), and temperature (`temperature`) forecast sensors.
- **`detailedForecast` / `detailedHourly` attribute** - the format used by [ha-solcast-solar](https://github.com/BJReplay/ha-solcast-solar)'s energy forecast sensors (`period_start` + `pv_estimate`, in kW). The kW value is automatically scaled ×1000 to match the Watt-based `INVERTER_POWER_FORECAST` field. Solcast has no clear-sky or temperature forecast, so only `INVERTER_POWER_FORECAST` is populated from it.
- **A `weather.*` entity** for `OUTDOOR_TEMP_FORECAST` - the integration calls the `weather.get_forecasts` service (`type: hourly`) and writes the returned temperature series.

Not currently supported: the built-in **forecast.solar** integration. It doesn't expose any per-entity forecast attribute - only a config-entry-level "solar forecast" API (used by the Energy dashboard) that returns Wh energy totals, which is a different mechanism this attribute-based batching can't use.

If the mapped entity has none of the above (e.g. right after Home Assistant/the integration restarts, before the source has produced its first forecast), nothing is written for that point in time; the next update from the source is picked up automatically, including immediately on integration startup if data is already available.

## Development

```bash
scripts/setup    # Install/update dependencies into .venv
scripts/test     # Run the test suite (supports pytest args, e.g. scripts/test -v)
scripts/lint     # Format and lint the codebase with ruff
scripts/develop  # Start Home Assistant with the integration loaded
```

## Troubleshooting

- **Setup error "Bucket not found"**: ensure the bucket exists and the token has write access to it.
- **TLS/certificate errors**: `https://` connections verify certificates; use a valid cert/CA, use `http://` for local non-TLS InfluxDB, or disable **Verify SSL certificate** (insecure).
- **`field type conflict` in InfluxDB**: this should no longer happen, since the integration auto-detects the existing field type from InfluxDB on startup. If it does, check that the configured token has **read** access to the bucket — without read, the integration cannot inspect the schema and falls back to its built-in defaults, which may not match.

### Sensor becomes `unknown`/`unavailable`

No point is written for a mapped entity while its state is `unknown`, `unavailable`, or missing entirely - the last value already in InfluxDB simply stays the latest point until the entity reports again. A warning is logged once per outage (not repeated on every 5-minute heartbeat), and an info message once the entity recovers, e.g.:

```
WARNING ... INVERTER_POWER (sensor.inverter_power) is unavailable; no new points will be written until it reports a value again
INFO    ... INVERTER_POWER (sensor.inverter_power) is reporting again
```

### Debug logging

To see exactly what the integration sends to InfluxDB, enable debug logging for it in `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  logs:
    custom_components.solectrus: debug
```

Every point is logged right before it's written - including ones that end up failing and get retried on the next batch - so you can trace mappings back to what actually landed (or didn't) in InfluxDB:

```
Influx point: sensor=INVERTER_POWER_FORECAST measurement=inverter_forecast field=power value=1500 timestamp=2026-07-29T13:00:00+00:00
Influx batch write succeeded: 3 point(s) sent
```
