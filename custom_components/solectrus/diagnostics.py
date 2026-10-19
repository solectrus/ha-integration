"""Diagnostics support for the SOLECTRUS integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_TOKEN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import SolectrusConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: SolectrusConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    manager = runtime.manager

    sensors_info = {}
    for key, sensor in manager._sensors.items():  # noqa: SLF001
        sensors_info[key] = {
            "entity_id": sensor.entity_id,
            "measurement": sensor.measurement,
            "field": sensor.field,
            "data_type": sensor.data_type,
            "last_value": sensor.last_value,
            "last_timestamp": (
                sensor.last_timestamp.isoformat() if sensor.last_timestamp else None
            ),
        }

    return {
        "config_entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "config_entry_options": dict(entry.options),
        "sensors": sensors_info,
        "pending_points": len(manager._pending),  # noqa: SLF001
    }
