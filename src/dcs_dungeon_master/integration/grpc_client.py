"""DCS-gRPC client and stream-ready wrapper."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Callable

import grpc

from dcs_dungeon_master.core.config import GrpcConfig
from dcs_dungeon_master.core.exceptions import IntegrationError
from dcs_dungeon_master.integration.grpc_codegen import GrpcContractModules, import_generated_contracts
from dcs_dungeon_master.integration.normalizers import (
    normalize_grpc_mission_event,
    normalize_grpc_mission_metadata,
    normalize_grpc_unit_event,
)
from dcs_dungeon_master.integration.types import (
    GrpcMissionMetadataSnapshot,
    GrpcStreamEnvelope,
    IntegrationHealthStatus,
)


StubFactory = Callable[[grpc.Channel, GrpcContractModules], object]
ChannelFactory = Callable[[GrpcConfig], grpc.Channel]
ChannelReady = Callable[[grpc.Channel, float], None]


class DcsGrpcClient:
    """Streaming-ready gRPC wrapper for Phase 1 groundwork."""

    def __init__(
        self,
        config: GrpcConfig,
        *,
        stub_factory: StubFactory | None = None,
        channel_factory: ChannelFactory | None = None,
        channel_ready: ChannelReady | None = None,
        contract_loader: Callable[[], GrpcContractModules] | None = None,
    ) -> None:
        self._config = config
        self._stub_factory = stub_factory or self._default_stub_factory
        self._channel_factory = channel_factory or self._default_channel_factory
        self._channel_ready = channel_ready or self._wait_for_channel_ready
        self._contract_loader = contract_loader or import_generated_contracts

    @property
    def endpoint(self) -> str:
        return f"{self._config.host}:{self._config.port}"

    def build_channel(self) -> grpc.Channel:
        return self._channel_factory(self._config)

    def _default_channel_factory(self, config: GrpcConfig) -> grpc.Channel:
        target = self.endpoint
        options = []
        if config.server_host_override:
            options.append(("grpc.ssl_target_name_override", config.server_host_override))
        if config.secure:
            return grpc.secure_channel(target, grpc.ssl_channel_credentials(), options=options)
        return grpc.insecure_channel(target, options=options)

    def check_health(self) -> IntegrationHealthStatus:
        channel = self.build_channel()
        try:
            self._channel_ready(channel, self._config.timeout_sec)
        except Exception as exc:  # noqa: BLE001
            return IntegrationHealthStatus(
                service="dcs_grpc",
                endpoint=self.endpoint,
                healthy=False,
                detail=str(exc),
            )
        finally:
            channel.close()
        return IntegrationHealthStatus(
            service="dcs_grpc",
            endpoint=self.endpoint,
            healthy=True,
            detail="channel_ready",
        )

    def get_mission_metadata(self) -> GrpcMissionMetadataSnapshot:
        contracts = self._contract_loader()
        channel = self.build_channel()
        try:
            stub = self._stub_factory(channel, contracts)
            response = stub.GetMissionMetadata(
                contracts.mission_pb2.MissionMetadataRequest(),
                timeout=self._config.timeout_sec,
            )
            return normalize_grpc_mission_metadata(response)
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"DCS-gRPC metadata request failed: {exc}") from exc
        finally:
            channel.close()

    def iter_unit_events(self, *, poll_rate: float = 1.0, coalition: str = "") -> Iterator[GrpcStreamEnvelope]:
        contracts = self._contract_loader()
        channel = self.build_channel()
        try:
            stub = self._stub_factory(channel, contracts)
            request = contracts.mission_pb2.StreamUnitsRequest(poll_rate=poll_rate, coalition=coalition)
            for message in stub.StreamUnits(request, timeout=self._config.timeout_sec):
                yield normalize_grpc_unit_event(message)
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"DCS-gRPC unit stream failed: {exc}") from exc
        finally:
            channel.close()

    def iter_mission_events(self) -> Iterator[GrpcStreamEnvelope]:
        contracts = self._contract_loader()
        channel = self.build_channel()
        try:
            stub = self._stub_factory(channel, contracts)
            request = contracts.mission_pb2.StreamEventsRequest()
            for message in stub.StreamEvents(request, timeout=self._config.timeout_sec):
                yield normalize_grpc_mission_event(message)
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"DCS-gRPC event stream failed: {exc}") from exc
        finally:
            channel.close()

    @staticmethod
    def _wait_for_channel_ready(channel: grpc.Channel, timeout: float) -> None:
        grpc.channel_ready_future(channel).result(timeout=timeout)

    @staticmethod
    def _default_stub_factory(channel: grpc.Channel, contracts: GrpcContractModules) -> object:
        return contracts.mission_pb2_grpc.MissionServiceStub(channel)
