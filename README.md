# SOLECTRUS Home Assistant Integration

This custom integration forwards Home Assistant entity values to the external SOLECTRUS instance. It is tailored for the ~40 known SOLECTRUS sensors plus 20 custom slots so you can map each one to an entity, optionally overriding measurement and field names.

## Features

- Configure InfluxDB URL, token, organisation, and bucket directly in the config flow.
- Map every SOLECTRUS sensor (and 20 custom sensors) to a Home Assistant entity via the options flow; measurement/field defaults are pre-filled but can be overridden.
- Writes are throttled to no more than once every 5 seconds per sensor and at least once every 5 minutes (last known value), skipping repeated zero values.

## Setup

1. Install the integration (e.g. via HACS or as a custom component in `custom_components/solectrus_integration`).
2. Add the integration in Home Assistant and enter your InfluxDB connection details.
3. Open the integration options to map the provided SOLECTRUS sensor keys to the Home Assistant entities you want to forward. Leave measurement/field empty to use defaults.

Once configured, the integration listens for entity state changes and writes them to InfluxDB following the above rules.
