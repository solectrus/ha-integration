"""Custom types for solectrus_integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import SolectrusInfluxClient
    from .manager import SensorManager


type SolectrusConfigEntry = ConfigEntry[SolectrusRuntimeData]


@dataclass
class SolectrusRuntimeData:
    """Runtime data stored on the config entry."""

    client: SolectrusInfluxClient
    manager: SensorManager
