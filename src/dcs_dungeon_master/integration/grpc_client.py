"""DCS-gRPC client and stream-ready wrapper."""

from __future__ import annotations

from collections.abc import Iterator
import time
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
    IntegrationStreamStatus,
)


StubFactory = Callable[[grpc.Channel, GrpcContractModules], object]
ChannelFactory = Callable[[GrpcConfig], grpc.Channel]
ChannelReady = Callable[[grpc.Channel, float], None]
SleepFn = Callable[[float], None]


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
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._config = config
        self._stub_factory = stub_factory or self._default_stub_factory
        self._channel_factory = channel_factory or self._default_channel_factory
        self._channel_ready = channel_ready or self._wait_for_channel_ready
        self._contract_loader = contract_loader or import_generated_contracts
        self._sleep = sleep_fn or time.sleep
        self._last_stream_statuses: dict[str, IntegrationStreamStatus] = {}

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
        config_error = self._validate_config()
        if config_error is not None:
            return IntegrationHealthStatus(
                service="dcs_grpc",
                endpoint=self.endpoint,
                healthy=False,
                detail=config_error,
                status="config_invalid",
                attempt_count=0,
            )
        for attempt in range(1, max(self._config.retry_attempts, 1) + 2):
            channel = self.build_channel()
            try:
                self._channel_ready(channel, self._config.timeout_sec)
                detail = "channel_ready" if attempt == 1 else "channel_ready_after_retry"
                return IntegrationHealthStatus(
                    service="dcs_grpc",
                    endpoint=self.endpoint,
                    healthy=True,
                    detail=detail,
                    status="healthy",
                    attempt_count=attempt,
                )
            except Exception as exc:  # noqa: BLE001
                if attempt > self._config.retry_attempts:
                    return IntegrationHealthStatus(
                        service="dcs_grpc",
                        endpoint=self.endpoint,
                        healthy=False,
                        detail=str(exc),
                        status="unreachable",
                        attempt_count=attempt,
                    )
                self._last_stream_statuses["health_check"] = IntegrationStreamStatus(
                    service="dcs_grpc",
                    stream_name="health_check",
                    endpoint=self.endpoint,
                    status="retrying",
                    detail=str(exc),
                    attempt_count=attempt,
                )
                self._sleep(self._backoff_delay(attempt))
            finally:
                channel.close()

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
        yield from self._iter_stream_with_retry(
            "unit_events",
            lambda contracts: contracts.mission_pb2.StreamUnitsRequest(poll_rate=poll_rate, coalition=coalition),
            "StreamUnits",
            normalize_grpc_unit_event,
        )

    def iter_mission_events(self) -> Iterator[GrpcStreamEnvelope]:
        yield from self._iter_stream_with_retry(
            "mission_events",
            lambda contracts: contracts.mission_pb2.StreamEventsRequest(),
            "StreamEvents",
            normalize_grpc_mission_event,
        )

    def get_stream_status(self, stream_name: str) -> IntegrationStreamStatus | None:
        return self._last_stream_statuses.get(stream_name)

    def _iter_stream_with_retry(
        self,
        stream_name: str,
        request_factory: Callable[[GrpcContractModules], object],
        rpc_name: str,
        normalizer: Callable[[object], GrpcStreamEnvelope],
    ) -> Iterator[GrpcStreamEnvelope]:
        config_error = self._validate_config()
        if config_error is not None:
            status = IntegrationStreamStatus(
                service="dcs_grpc",
                stream_name=stream_name,
                endpoint=self.endpoint,
                status="config_invalid",
                detail=config_error,
                attempt_count=0,
            )
            self._last_stream_statuses[stream_name] = status
            raise IntegrationError(f"DCS-gRPC {stream_name} stream failed: {config_error}")

        contracts = self._contract_loader()
        for attempt in range(1, max(self._config.retry_attempts, 1) + 2):
            channel = self.build_channel()
            try:
                stub = self._stub_factory(channel, contracts)
                request = request_factory(contracts)
                stream = getattr(stub, rpc_name)(request, timeout=self._config.timeout_sec)
                for message in stream:
                    self._last_stream_statuses[stream_name] = IntegrationStreamStatus(
                        service="dcs_grpc",
                        stream_name=stream_name,
                        endpoint=self.endpoint,
                        status="healthy",
                        detail="stream_active",
                        attempt_count=attempt,
                    )
                    yield normalizer(message)
                self._last_stream_statuses[stream_name] = IntegrationStreamStatus(
                    service="dcs_grpc",
                    stream_name=stream_name,
                    endpoint=self.endpoint,
                    status="healthy",
                    detail="stream_closed_cleanly",
                    attempt_count=attempt,
                )
                return
            except Exception as exc:  # noqa: BLE001
                if attempt > self._config.retry_attempts:
                    status = IntegrationStreamStatus(
                        service="dcs_grpc",
                        stream_name=stream_name,
                        endpoint=self.endpoint,
                        status="failed",
                        detail=str(exc),
                        attempt_count=attempt,
                    )
                    self._last_stream_statuses[stream_name] = status
                    raise IntegrationError(
                        f"DCS-gRPC {stream_name} stream failed: status={status.status}; "
                        f"attempts={status.attempt_count}; detail={status.detail}"
                    ) from exc
                self._last_stream_statuses[stream_name] = IntegrationStreamStatus(
                    service="dcs_grpc",
                    stream_name=stream_name,
                    endpoint=self.endpoint,
                    status="retrying",
                    detail=str(exc),
                    attempt_count=attempt,
                )
                self._sleep(self._backoff_delay(attempt))
            finally:
                channel.close()

    def _validate_config(self) -> str | None:
        if not self._config.host.strip():
            return "gRPC host is empty."
        if self._config.port <= 0:
            return "gRPC port must be positive."
        return None

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(0.05 * attempt, 0.2)

    @staticmethod
    def _wait_for_channel_ready(channel: grpc.Channel, timeout: float) -> None:
        grpc.channel_ready_future(channel).result(timeout=timeout)

    @staticmethod
    def _default_stub_factory(channel: grpc.Channel, contracts: GrpcContractModules) -> object:
        return contracts.mission_pb2_grpc.MissionServiceStub(channel)
