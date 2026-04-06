"""Integration ingest coordinator for world-state updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from typing import TYPE_CHECKING

from dcs_dungeon_master.core.models import IngestCycleResult, NormalizedIntegrationBatch
from dcs_dungeon_master.world_state import WorldStateUpdater

if TYPE_CHECKING:
    from dcs_dungeon_master.integration import IntegrationServices


@dataclass(slots=True)
class IntegrationIngestCoordinator:
    integrations: IntegrationServices
    world_updater: WorldStateUpdater
    max_grpc_events_per_stream: int = 5

    @property
    def status(self) -> str:
        return "integration-ingest-ready"

    def ingest_once(
        self,
        run_id: str,
        *,
        occurred_at: datetime | None = None,
        persist: bool = True,
    ) -> IngestCycleResult:
        timestamp = occurred_at or datetime.now(UTC)
        mission_snapshot = None
        unit_snapshots = ()
        airfield_snapshots = ()
        grpc_metadata = None
        grpc_events = []
        olympus_available = True
        grpc_available = True
        errors: list[str] = []

        try:
            mission_snapshot = self.integrations.olympus.get_mission_snapshot()
            unit_snapshots = self.integrations.olympus.get_units_snapshot()
            airfield_snapshots = self.integrations.olympus.get_airfields_snapshot()
        except Exception as exc:  # noqa: BLE001
            olympus_available = False
            errors.append(f"olympus:{exc}")

        try:
            grpc_metadata = self.integrations.dcs_grpc.get_mission_metadata()
        except Exception as exc:  # noqa: BLE001
            grpc_available = False
            errors.append(f"grpc_metadata:{exc}")

        try:
            grpc_events.extend(
                islice(self.integrations.dcs_grpc.iter_unit_events(poll_rate=0.1), self.max_grpc_events_per_stream)
            )
        except Exception as exc:  # noqa: BLE001
            grpc_available = False
            errors.append(f"grpc_unit_events:{exc}")

        try:
            grpc_events.extend(islice(self.integrations.dcs_grpc.iter_mission_events(), self.max_grpc_events_per_stream))
        except Exception as exc:  # noqa: BLE001
            grpc_available = False
            errors.append(f"grpc_mission_events:{exc}")

        snapshot = self.world_updater.apply_integration_updates(
            run_id,
            mission_snapshot=mission_snapshot,
            unit_snapshots=tuple(unit_snapshots),
            airfield_snapshots=tuple(airfield_snapshots),
            grpc_metadata=grpc_metadata,
            grpc_events=tuple(grpc_events),
            occurred_at=timestamp,
        )
        result = IngestCycleResult(
            id=None,
            run_id=run_id,
            occurred_at=timestamp,
            mission_time=snapshot.mission_time,
            olympus_available=olympus_available,
            grpc_available=grpc_available,
            normalized_batch=NormalizedIntegrationBatch(
                mission_snapshot_present=mission_snapshot is not None,
                unit_snapshot_count=len(unit_snapshots),
                airfield_snapshot_count=len(airfield_snapshots),
                grpc_metadata_present=grpc_metadata is not None,
                grpc_event_count=len(grpc_events),
            ),
            error_classification="partial_source_failure" if errors else None,
            error_detail="; ".join(errors) if errors else None,
        )
        if persist:
            persisted_id = self.world_updater.repository.store.save_ingest_cycle_result(result)
            result = IngestCycleResult(
                id=persisted_id,
                run_id=result.run_id,
                occurred_at=result.occurred_at,
                mission_time=result.mission_time,
                olympus_available=result.olympus_available,
                grpc_available=result.grpc_available,
                normalized_batch=result.normalized_batch,
                error_classification=result.error_classification,
                error_detail=result.error_detail,
            )
        return result
