"""Helpers for vendored DCS-gRPC proto generation and import."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import subprocess
import sys

from dcs_dungeon_master.core.exceptions import ProtoGenerationError


PROTO_ROOT = Path("vendor/dcs_grpc/protos")
DEFAULT_OUTPUT_ROOT = Path("src")
MISSION_PROTO = PROTO_ROOT / "dcs/mission/v0/mission.proto"
COMMON_PROTO = PROTO_ROOT / "dcs/common/v0/common.proto"


@dataclass(slots=True, frozen=True)
class GrpcContractModules:
    mission_pb2: object
    mission_pb2_grpc: object
    common_pb2: object


def generated_stub_paths(output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> tuple[Path, ...]:
    output_path = Path(output_root)
    return (
        output_path / "dcs/common/v0/common_pb2.py",
        output_path / "dcs/mission/v0/mission_pb2.py",
        output_path / "dcs/mission/v0/mission_pb2_grpc.py",
    )


def generated_stubs_exist(output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> bool:
    return all(path.exists() for path in generated_stub_paths(output_root))


def import_generated_contracts() -> GrpcContractModules:
    try:
        return GrpcContractModules(
            mission_pb2=importlib.import_module("dcs.mission.v0.mission_pb2"),
            mission_pb2_grpc=importlib.import_module("dcs.mission.v0.mission_pb2_grpc"),
            common_pb2=importlib.import_module("dcs.common.v0.common_pb2"),
        )
    except ModuleNotFoundError as exc:
        raise ProtoGenerationError(
            "Vendored DCS-gRPC stubs are not available. Run "
            "`dcs-dungeon-master validate-grpc-contracts` or generate stubs into `src/` first."
        ) from exc


def generate_vendored_stubs(output_root: str | Path = DEFAULT_OUTPUT_ROOT) -> tuple[Path, ...]:
    output_path = Path(output_root)
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_ROOT}",
        f"--python_out={output_path}",
        f"--grpc_python_out={output_path}",
        str(COMMON_PROTO),
        str(MISSION_PROTO),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise ProtoGenerationError(exc.stderr or exc.stdout or "grpc_tools.protoc failed") from exc
    return generated_stub_paths(output_path)
