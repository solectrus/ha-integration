"""InfluxDB access layer for the SOLECTRUS integration."""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi
from influxdb_client.rest import ApiException
from urllib3.exceptions import HTTPError

from .const import LOGGER

if TYPE_CHECKING:
    from datetime import datetime

# HTTP status codes
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403


class SolectrusInfluxError(Exception):
    """Base exception for InfluxDB issues."""


class SolectrusConnectionError(SolectrusInfluxError):
    """Raised when connection to InfluxDB fails."""


class SolectrusAuthError(SolectrusInfluxError):
    """Raised when authentication fails."""


class SolectrusInfluxClient:
    """Thin wrapper around the sync InfluxDB client, executed off the event loop."""

    def __init__(self, url: str, token: str, org: str, bucket: str) -> None:
        """Create the Influx client wrapper."""
        self._url = url
        self._token = token
        self._org = org
        self._bucket = bucket
        self._client: InfluxDBClient | None = None
        self._write_api: WriteApi | None = None
        self._ssl = not url.lower().startswith("http://")

    async def async_validate_connection(self) -> None:
        """Validate connectivity, auth, and bucket access."""
        client = await self._ensure_client()
        loop = asyncio.get_running_loop()
        try:
            bucket = await loop.run_in_executor(
                None, lambda: client.buckets_api().find_bucket_by_name(self._bucket)
            )
        except ApiException as err:
            if err.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
                msg = "Invalid token or insufficient permissions"
                raise SolectrusAuthError(msg) from err
            msg = f"API error: {err}"
            raise SolectrusInfluxError(msg) from err
        except (HTTPError, OSError) as err:
            msg = f"Connection failed: {err}"
            raise SolectrusConnectionError(msg) from err

        if bucket is None:
            msg = "Bucket not found or token lacks permission"
            raise SolectrusInfluxError(msg)

    async def async_connect(self) -> None:
        """Prepare the write API."""
        client = await self._ensure_client()
        if self._write_api is None:
            loop = asyncio.get_running_loop()
            self._write_api = await loop.run_in_executor(
                None, lambda: client.write_api(write_options=SYNCHRONOUS)
            )

    async def async_write(
        self,
        measurement: str,
        field: str,
        value: Any,
        timestamp: datetime | None = None,
    ) -> None:
        """Write a point to InfluxDB."""
        if self._write_api is None:
            await self.async_connect()

        point = Point(measurement)
        point.field(field, value)
        if timestamp is not None:
            point.time(timestamp, WritePrecision.S)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._write_api.write(
                    bucket=self._bucket,
                    org=self._org,
                    record=point,
                    write_precision=WritePrecision.S,
                ),
            )
        except ApiException as err:
            if err.status == _HTTP_UNAUTHORIZED:
                LOGGER.error("InfluxDB authentication failed")
                raise SolectrusAuthError("Authentication failed") from err
            LOGGER.error("InfluxDB API error: %s", err)
            raise SolectrusInfluxError(f"API error: {err}") from err
        except (HTTPError, OSError) as err:
            LOGGER.warning("InfluxDB connection failed: %s", err)
            raise SolectrusConnectionError(f"Connection failed: {err}") from err

    async def async_write_batch(self, points: list[Point]) -> None:
        """Write multiple points to InfluxDB in a single request."""
        if not points:
            return

        if self._write_api is None:
            await self.async_connect()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._write_api.write(
                    bucket=self._bucket,
                    org=self._org,
                    record=points,
                    write_precision=WritePrecision.S,
                ),
            )
        except ApiException as err:
            if err.status == _HTTP_UNAUTHORIZED:
                LOGGER.error("InfluxDB authentication failed")
                raise SolectrusAuthError("Authentication failed") from err
            LOGGER.error("InfluxDB API error: %s", err)
            raise SolectrusInfluxError(f"API error: {err}") from err
        except (HTTPError, OSError) as err:
            LOGGER.warning("InfluxDB connection failed: %s", err)
            raise SolectrusConnectionError(f"Connection failed: {err}") from err

    async def async_close(self) -> None:
        """Close the client."""
        client = self._client
        self._client = None
        self._write_api = None
        if client is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.close)

    async def _ensure_client(self) -> InfluxDBClient:
        """Create the client off the event loop to avoid blocking."""
        if self._client is not None:
            return self._client

        loop = asyncio.get_running_loop()

        def _build_client() -> InfluxDBClient:
            ssl_param: bool | ssl.SSLContext = (
                ssl.create_default_context() if self._ssl else False
            )
            return InfluxDBClient(
                url=self._url,
                token=self._token,
                org=self._org,
                ssl=ssl_param,
                verify_ssl=self._ssl,
            )

        try:
            self._client = await loop.run_in_executor(None, _build_client)
        except (HTTPError, OSError) as err:
            msg = f"Connection failed: {err}"
            raise SolectrusConnectionError(msg) from err
        except (ValueError, TypeError) as err:
            msg = f"Invalid configuration: {err}"
            raise SolectrusInfluxError(msg) from err
        return self._client
