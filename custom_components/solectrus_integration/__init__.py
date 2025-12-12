"""SOLECTRUS integration entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .api import SolectrusInfluxClient
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
    )

    sensors = _build_sensor_map(entry)
    manager = SensorManager(hass, client, sensors)
    await manager.async_start()

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
    """Create ConfiguredSensor objects keyed by entity_id."""
    sensors: dict[str, ConfiguredSensor] = {}
    configured_sensors: dict = entry.options.get(CONF_SENSORS, {})

    for key, settings in configured_sensors.items():
        entity_id = settings.get(CONF_ENTITY_ID)
        if not entity_id:
            continue

        defaults = SENSOR_DEFINITIONS.get(key)
        measurement = settings.get(
            CONF_MEASUREMENT, defaults.measurement if defaults else key.lower()
        )
        field = settings.get(CONF_FIELD, defaults.field if defaults else "value")
        data_type = settings.get(
            CONF_DATA_TYPE, defaults.data_type if defaults else "float"
        )
        sensors[entity_id] = ConfiguredSensor(
            key=key,
            entity_id=entity_id,
            measurement=measurement,
            field=field,
            data_type=data_type,
        )

    return sensors
