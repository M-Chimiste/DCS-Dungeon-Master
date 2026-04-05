from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from dcs_dungeon_master.core.config import GrpcConfig
from dcs_dungeon_master.integration.grpc_client import DcsGrpcClient
from dcs_dungeon_master.integration.grpc_codegen import GrpcContractModules, generate_vendored_stubs


class FakeChannel:
    def close(self) -> None:
        self.closed = True


class FakeMissionServiceStub:
    def GetMissionMetadata(self, request, timeout: float):
        assert request.__class__.__name__ == "MissionMetadataRequest"
        assert timeout == 4.0
        return SimpleNamespace(theater="Persian Gulf", mission_name="Phase 1", mission_time="12:00:00Z")

    def StreamUnits(self, request, timeout: float):
        assert request.poll_rate == 2.0
        assert request.coalition == "blue"
        assert timeout == 4.0
        return iter(
            [
                SimpleNamespace(
                    unit_id="blue_armor_1",
                    coalition="blue",
                    category="ground",
                    name="Blue Armor",
                    lat=24.1,
                    lng=54.3,
                    alt=0.0,
                )
            ]
        )

    def StreamEvents(self, request, timeout: float):
        assert request.__class__.__name__ == "StreamEventsRequest"
        assert timeout == 4.0
        return iter([SimpleNamespace(event_id="evt-1", event_type="contact", coalition="red", description="Ping")])


@dataclass(slots=True)
class MissionMetadataRequest:
    pass


@dataclass(slots=True)
class StreamUnitsRequest:
    poll_rate: float
    coalition: str


@dataclass(slots=True)
class StreamEventsRequest:
    pass


def _fake_contracts() -> GrpcContractModules:
    mission_pb2 = SimpleNamespace(
        MissionMetadataRequest=MissionMetadataRequest,
        StreamUnitsRequest=StreamUnitsRequest,
        StreamEventsRequest=StreamEventsRequest,
    )
    mission_pb2_grpc = SimpleNamespace(MissionServiceStub=lambda channel: FakeMissionServiceStub())
    common_pb2 = SimpleNamespace()
    return GrpcContractModules(
        mission_pb2=mission_pb2,
        mission_pb2_grpc=mission_pb2_grpc,
        common_pb2=common_pb2,
    )


def test_dcs_grpc_health_check_supports_remote_endpoint() -> None:
    captured: list[GrpcConfig] = []

    def channel_factory(config: GrpcConfig):
        captured.append(config)
        return FakeChannel()

    client = DcsGrpcClient(
        GrpcConfig(
            host="192.168.10.50",
            port=50051,
            timeout_sec=3.0,
            secure=True,
            server_host_override="dcs.internal",
        ),
        channel_factory=channel_factory,
        channel_ready=lambda channel, timeout: None,
    )

    health = client.check_health()

    assert health.healthy is True
    assert health.endpoint == "192.168.10.50:50051"
    assert captured[0].server_host_override == "dcs.internal"


def test_dcs_grpc_wrapper_normalizes_metadata_and_streams() -> None:
    client = DcsGrpcClient(
        GrpcConfig(host="127.0.0.1", port=50051, timeout_sec=4.0),
        channel_factory=lambda config: FakeChannel(),
        channel_ready=lambda channel, timeout: None,
        contract_loader=_fake_contracts,
    )

    metadata = client.get_mission_metadata()
    unit_events = list(client.iter_unit_events(poll_rate=2.0, coalition="blue"))
    mission_events = list(client.iter_mission_events())

    assert metadata.theater == "Persian Gulf"
    assert metadata.mission_name == "Phase 1"
    assert unit_events[0].stream_name == "stream_units"
    assert unit_events[0].payload["unit_id"] == "blue_armor_1"
    assert mission_events[0].event_type == "contact"
    assert mission_events[0].payload["description"] == "Ping"


def test_generate_vendored_stubs_creates_python_modules(tmp_path: Path) -> None:
    generated = generate_vendored_stubs(tmp_path)

    assert len(generated) == 3
    assert all(path.exists() for path in generated)
