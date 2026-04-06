"""Normalization helpers for Olympus and gRPC payloads."""

from __future__ import annotations

from typing import Any

from dcs_dungeon_master.integration.types import (
    GrpcMissionMetadataSnapshot,
    GrpcStreamEnvelope,
    OlympusAirfieldSnapshot,
    OlympusMissionSnapshot,
    OlympusUnitSnapshot,
)


def normalize_olympus_mission(payload: dict[str, Any]) -> OlympusMissionSnapshot:
    weather = payload.get("weather")
    weather_summary = None
    if isinstance(weather, dict):
        parts = []
        if isinstance(weather.get("visibility"), str):
            parts.append(weather["visibility"])
        if isinstance(weather.get("winds"), str):
            parts.append(weather["winds"])
        weather_summary = ", ".join(parts) if parts else None
    return OlympusMissionSnapshot(
        theater=_as_optional_str(payload.get("theater") or payload.get("map")),
        mission_name=_as_optional_str(payload.get("mission_name") or payload.get("name")),
        mission_time=_as_optional_str(payload.get("mission_time") or payload.get("time")),
        weather_summary=weather_summary,
    )


def normalize_olympus_units(payload: dict[str, Any]) -> tuple[OlympusUnitSnapshot, ...]:
    units_raw = payload.get("units", payload if isinstance(payload, list) else [])
    if not isinstance(units_raw, list):
        return ()
    snapshots = []
    for item in units_raw:
        if not isinstance(item, dict):
            continue
        position = item.get("position", {})
        if not isinstance(position, dict):
            position = {}
        snapshots.append(
            OlympusUnitSnapshot(
                unit_id=_as_optional_str(item.get("id") or item.get("unit_id")) or "unknown",
                name=_as_optional_str(item.get("name")),
                unit_type=_as_optional_str(item.get("type")),
                category=_as_optional_str(item.get("category")),
                coalition=_as_optional_str(item.get("coalition")),
                lat=_as_optional_float(position.get("lat") if position else item.get("lat")),
                lng=_as_optional_float(position.get("lng") if position else item.get("lng")),
                alt=_as_optional_float(position.get("alt") if position else item.get("alt")),
                heading=_as_optional_float(item.get("heading")),
                speed=_as_optional_float(item.get("speed")),
                health=_as_optional_float(item.get("health")),
            )
        )
    return tuple(snapshots)


def normalize_olympus_airfields(payload: dict[str, Any]) -> tuple[OlympusAirfieldSnapshot, ...]:
    airfields_raw = payload.get("airfields", payload if isinstance(payload, list) else [])
    if not isinstance(airfields_raw, list):
        return ()
    snapshots = []
    for item in airfields_raw:
        if not isinstance(item, dict):
            continue
        snapshots.append(
            OlympusAirfieldSnapshot(
                airfield_id=_as_optional_str(item.get("id") or item.get("airfield_id")) or "unknown",
                name=_as_optional_str(item.get("name")),
                owner=_as_optional_str(item.get("owner")),
                runway_status=_as_optional_str(item.get("runway_status")),
            )
        )
    return tuple(snapshots)


def normalize_grpc_mission_metadata(response: Any) -> GrpcMissionMetadataSnapshot:
    return GrpcMissionMetadataSnapshot(
        theater=getattr(response, "theater", ""),
        mission_name=getattr(response, "mission_name", ""),
        mission_time=getattr(response, "mission_time", ""),
    )


def normalize_grpc_unit_event(message: Any) -> GrpcStreamEnvelope:
    return GrpcStreamEnvelope(
        stream_name="stream_units",
        entity_id=str(getattr(message, "unit_id", "")),
        event_type="unit",
        coalition=_as_optional_str(getattr(message, "coalition", None)),
        payload={
            "unit_id": getattr(message, "unit_id", ""),
            "coalition": getattr(message, "coalition", ""),
            "category": getattr(message, "category", ""),
            "name": getattr(message, "name", ""),
            "lat": getattr(message, "lat", 0.0),
            "lng": getattr(message, "lng", 0.0),
            "alt": getattr(message, "alt", 0.0),
        },
    )


def normalize_grpc_mission_event(message: Any) -> GrpcStreamEnvelope:
    return GrpcStreamEnvelope(
        stream_name="stream_events",
        entity_id=str(getattr(message, "event_id", "")),
        event_type=getattr(message, "event_type", ""),
        coalition=_as_optional_str(getattr(message, "coalition", None)),
        payload={
            "event_id": getattr(message, "event_id", ""),
            "event_type": getattr(message, "event_type", ""),
            "coalition": getattr(message, "coalition", ""),
            "description": getattr(message, "description", ""),
        },
    )


def _as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _as_optional_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
