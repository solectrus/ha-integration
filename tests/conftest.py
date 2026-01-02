"""Pytest configuration for SOLECTRUS tests."""

import pytest


@pytest.fixture
def sample_forecast_list():
    """Sample forecast data for testing."""
    return [
        {"datetime": "2024-01-15T12:00:00+00:00", "power": 1500, "temperature": 5.0},
        {"datetime": "2024-01-15T13:00:00+00:00", "power": 1800, "temperature": 6.5},
        {"datetime": "2024-01-15T14:00:00+00:00", "power": 1200, "temperature": 7.0},
    ]
