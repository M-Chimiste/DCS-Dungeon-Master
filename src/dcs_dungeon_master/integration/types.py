"""Transport-facing integration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class IntegrationHealthStatus:
    service: str
    endpoint: str
    healthy: bool
    detail: str


@dataclass(slots=True, frozen=True)
class OlympusMissionSnapshot:
    theater: str | None = None
    mission_name: str | None = None
    mission_time: str | None = None
    weather_summary: str | None = None


@dataclass(slots=True, frozen=True)
class OlympusUnitSnapshot:
    unit_id: str
    name: str | None
    unit_type: str | None
    category: str | None
    coalition: str | None
    lat: float | None
    lng: float | None
    alt: float | None
    heading: float | None = None
    speed: float | None = None
    health: float | None = None


@dataclass(slots=True, frozen=True)
class OlympusAirfieldSnapshot:
    airfield_id: str
    name: str | None
    owner: str | None
    runway_status: str | None = None


@dataclass(slots=True, frozen=True)
class OlympusWriteRequest:
    method: str
    path: str
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GrpcMissionMetadataSnapshot:
    theater: str
    mission_name: str
    mission_time: str


@dataclass(slots=True, frozen=True)
class GrpcStreamEnvelope:
    stream_name: str
    entity_id: str
    event_type: str
    coalition: str | None
    payload: dict[str, Any]
