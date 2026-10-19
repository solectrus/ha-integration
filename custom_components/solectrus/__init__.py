"""SOLECTRUS integration entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryNotReady

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
    LOGGER,
    SENSOR_DEFINITIONS,
)
from .data import SolectrusConfigEntry, SolectrusRuntimeData
from .manager import ConfiguredSensor, SensorManager

if TYPE_CHECKING:
    from homeassistant.const import Platform
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolectrusConfigEntry,
) -> bool:
    """Set up the SOLECTRUS integration."""
    client = SolectrusInfluxClient(
        url=entry.data[CONF_URL],
        token=entry.data[CONF_TOKEN],
        org=entry.data[CONF_ORG],
        bucket=entry.data[CONF_BUCKET],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )

    sensors = _build_sensor_map(entry)
    manager = SensorManager(hass, client, sensors)
    try:
        await client.async_connect()
        await manager.async_start()
    except SolectrusInfluxError as err:
        await client.async_close()
        raise ConfigEntryNotReady(f"Cannot reach InfluxDB: {err}") from err

    entry.runtime_data = SolectrusRuntimeData(
        client=client,
        manager=manager,
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(
    _hass: HomeAssistant,
    entry: SolectrusConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    runtime = entry.runtime_data
    await runtime.manager.async_stop()
    await runtime.client.async_close()
    return True


async def async_reload_entry(
    hass: HomeAssistant,
    entry: SolectrusConfigEntry,
) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


def _build_sensor_map(entry: SolectrusConfigEntry) -> dict[str, ConfiguredSensor]:
    """Create ConfiguredSensor objects keyed by SOLECTRUS sensor key."""
    sensors: dict[str, ConfiguredSensor] = {}
    configured_sensors: dict = entry.options.get(CONF_SENSORS, {})

    for key, settings in configured_sensors.items():
        entity_id = settings.get(CONF_ENTITY_ID)
        if not entity_id:
            continue

        defaults = SENSOR_DEFINITIONS.get(key)
        if defaults is None:
            LOGGER.warning("Unknown sensor key %r in config; skipping", key)
            continue

        measurement = settings.get(CONF_MEASUREMENT, defaults.measurement)
        field = settings.get(CONF_FIELD, defaults.field)
        sensors[key] = ConfiguredSensor(
            key=key,
            entity_id=entity_id,
            measurement=measurement,
            field=field,
            data_type=defaults.data_type,
            min_value=defaults.min_value,
            max_value=defaults.max_value,
            attribute=defaults.attribute,
        )

    return sensors
