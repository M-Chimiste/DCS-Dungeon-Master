from __future__ import annotations

import httpx

from dcs_dungeon_master.core.config import OlympusConfig
from dcs_dungeon_master.integration.olympus import OlympusClient


def test_olympus_health_check_and_snapshots_normalize_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/olympus/mission":
            return httpx.Response(
                200,
                json={
                    "theater": "Persian Gulf",
                    "mission_name": "PG Phase 1",
                    "mission_time": "09:30:00Z",
                    "weather": {"visibility": "good", "winds": "light"},
                },
            )
        if request.url.path == "/olympus/units":
            return httpx.Response(
                200,
                json={
                    "units": [
                        {
                            "id": "red_sam_1",
                            "name": "Red SAM One",
                            "type": "SA-6",
                            "category": "air_defense",
                            "coalition": "red",
                            "position": {"lat": 25.1, "lng": 56.2, "alt": 120.0},
                            "heading": 90.0,
                            "speed": 0.0,
                            "health": 1.0,
                        }
                    ]
                },
            )
        if request.url.path == "/olympus/airfields":
            return httpx.Response(
                200,
                json={"airfields": [{"id": "blue_rear_airbase", "name": "Al Dhafra", "owner": "blue"}]},
            )
        return httpx.Response(404, json={"detail": "not found"})

    client = OlympusClient(
        OlympusConfig(base_url="http://olympus.test", timeout_sec=2.0, retry_attempts=1),
        transport=httpx.MockTransport(handler),
    )

    health = client.check_health()
    mission = client.get_mission_snapshot()
    units = client.get_units_snapshot()
    airfields = client.get_airfields_snapshot()

    assert health.healthy is True
    assert health.status == "healthy"
    assert health.endpoint == "http://olympus.test/olympus/mission"
    assert mission.theater == "Persian Gulf"
    assert mission.weather_summary == "good, light"
    assert len(units) == 1
    assert units[0].unit_id == "red_sam_1"
    assert units[0].lat == 25.1
    assert len(airfields) == 1
    assert airfields[0].airfield_id == "blue_rear_airbase"

    client.close()


def test_olympus_write_request_builds_auth_headers(monkeypatch) -> None:
    monkeypatch.setenv("OLYMPUS_TOKEN", "token-value")
    monkeypatch.setenv("OLYMPUS_PASSWORD", "secret")
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, json={"accepted": True})

    client = OlympusClient(
        OlympusConfig(
            base_url="http://olympus.test",
            timeout_sec=2.0,
            username="controller",
            token_env_var="OLYMPUS_TOKEN",
            password_env_var="OLYMPUS_PASSWORD",
        ),
        transport=httpx.MockTransport(handler),
    )

    request = client.build_write_request("/olympus/command", {"command": "noop"})
    payload = client.send_write_request(request)

    assert payload == {"accepted": True}
    assert (captured_headers.get("authorization") or captured_headers.get("Authorization")) == "Bearer token-value"
    assert (captured_headers.get("x-authorized") or captured_headers.get("X-Authorized")) == "controller"
    assert (captured_headers.get("x-olympus-password") or captured_headers.get("X-Olympus-Password")) == "secret"

    client.close()


def test_olympus_health_retry_classification_is_deterministic() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(503, json={"detail": "warming up"})
        return httpx.Response(200, json={"theater": "Persian Gulf"})

    client = OlympusClient(
        OlympusConfig(base_url="http://olympus.test", timeout_sec=2.0, retry_attempts=2),
        transport=httpx.MockTransport(handler),
    )

    health = client.check_health()

    assert health.healthy is True
    assert health.status == "healthy"
    assert health.attempt_count == 2
    assert "after retry" in health.detail
    client.close()
