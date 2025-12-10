"""InfluxDB access layer for the SOLECTRUS integration."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi

from .const import LOGGER


class SolectrusInfluxError(Exception):
    """Base exception for InfluxDB write issues."""


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
        except Exception as err:  # pylint: disable=broad-except
            message = f"Bucket lookup failed: {err}"
            raise SolectrusInfluxError(message) from err

        if bucket is None:
            message = "Bucket not found or token lacks permission to read it"
            raise SolectrusInfluxError(message)

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
    ) -> None:
        """Write a point to InfluxDB."""
        if self._write_api is None:
            await self.async_connect()

        point = Point(measurement)
        point.field(field, value)

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
        except Exception as err:  # pylint: disable=broad-except
            LOGGER.error("Failed to write point to InfluxDB: %s", err)
            raise SolectrusInfluxError(err) from err

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
        except Exception as err:  # pylint: disable=broad-except
            message = f"Failed to create InfluxDB client: {err}"
            raise SolectrusInfluxError(message) from err
        return self._client
