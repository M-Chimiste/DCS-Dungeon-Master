"""Olympus HTTP integration client."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import httpx

from dcs_dungeon_master.core.config import OlympusConfig
from dcs_dungeon_master.core.exceptions import IntegrationError
from dcs_dungeon_master.integration.normalizers import (
    normalize_olympus_airfields,
    normalize_olympus_mission,
    normalize_olympus_units,
)
from dcs_dungeon_master.integration.types import (
    IntegrationHealthStatus,
    OlympusAirfieldSnapshot,
    OlympusMissionSnapshot,
    OlympusUnitSnapshot,
    OlympusWriteRequest,
)


class OlympusClient:
    """Small Olympus client with read-side operations and future write transport support."""

    def __init__(self, config: OlympusConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_sec,
            transport=transport,
            verify=config.verify_tls,
            headers=self._build_headers(),
        )

    @property
    def endpoint(self) -> str:
        return self._config.base_url.rstrip("/")

    def close(self) -> None:
        self._client.close()

    def check_health(self) -> IntegrationHealthStatus:
        try:
            response = self._request("GET", self._config.health_path)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return IntegrationHealthStatus(
                service="olympus",
                endpoint=urljoin(f"{self.endpoint}/", self._config.health_path.lstrip("/")),
                healthy=False,
                detail=str(exc),
            )
        return IntegrationHealthStatus(
            service="olympus",
            endpoint=urljoin(f"{self.endpoint}/", self._config.health_path.lstrip("/")),
            healthy=True,
            detail=f"http {response.status_code}",
        )

    def get_mission_snapshot(self) -> OlympusMissionSnapshot:
        response = self._request("GET", "/olympus/mission")
        response.raise_for_status()
        return normalize_olympus_mission(response.json())

    def get_units_snapshot(self) -> tuple[OlympusUnitSnapshot, ...]:
        response = self._request("GET", "/olympus/units")
        response.raise_for_status()
        return normalize_olympus_units(response.json())

    def get_airfields_snapshot(self) -> tuple[OlympusAirfieldSnapshot, ...]:
        response = self._request("GET", "/olympus/airfields")
        response.raise_for_status()
        return normalize_olympus_airfields(response.json())

    def build_write_request(self, path: str, payload: dict[str, Any], *, method: str = "POST") -> OlympusWriteRequest:
        return OlympusWriteRequest(method=method.upper(), path=path, payload=payload, headers=self._build_headers())

    def send_write_request(self, request: OlympusWriteRequest) -> dict[str, Any]:
        response = self._request(request.method, request.path, json=request.payload, headers=request.headers)
        response.raise_for_status()
        return response.json()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for _ in range(max(self._config.retry_attempts, 1)):
            try:
                return self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
        raise IntegrationError(f"Olympus request failed after retries: {last_error}") from last_error

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._config.username:
            headers["X-Authorized"] = self._config.username
        if self._config.token_env_var:
            token = os.getenv(self._config.token_env_var)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        if self._config.password_env_var:
            password = os.getenv(self._config.password_env_var)
            if password:
                headers["X-Olympus-Password"] = password
        return headers
