"""Project-specific exceptions."""


class DcsDungeonMasterError(Exception):
    """Base exception for the backend."""


class ConfigError(DcsDungeonMasterError):
    """Raised when configuration is missing or invalid."""


class ScenarioNotFoundError(DcsDungeonMasterError):
    """Raised when a configured scenario cannot be resolved."""
