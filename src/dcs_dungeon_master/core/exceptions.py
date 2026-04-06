"""Project-specific exceptions."""


class DcsDungeonMasterError(Exception):
    """Base exception for the backend."""


class ConfigError(DcsDungeonMasterError):
    """Raised when configuration is missing or invalid."""


class ScenarioNotFoundError(DcsDungeonMasterError):
    """Raised when a configured scenario cannot be resolved."""


class PersistenceError(DcsDungeonMasterError):
    """Raised when persistence operations fail."""


class IntegrationError(DcsDungeonMasterError):
    """Raised when integration-layer operations fail."""


class ProtoGenerationError(DcsDungeonMasterError):
    """Raised when vendored gRPC contracts cannot be generated or imported."""
