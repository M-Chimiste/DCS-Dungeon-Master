"""Shared enums for Milestone 0 domain contracts."""

from __future__ import annotations

from enum import StrEnum


class Coalition(StrEnum):
    RED = "red"
    BLUE = "blue"


class KnowledgeLevel(StrEnum):
    UNKNOWN_PRESENCE = "unknown_presence"
    SUSPECTED_TYPE = "suspected_type"
    CLASSIFIED_TYPE = "classified_type"
    CONFIRMED_TYPE = "confirmed_type"


class ConfidenceBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InferenceMode(StrEnum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    RICH = "rich"


class ActionType(StrEnum):
    SET_SECTOR_PRIORITY = "set_sector_priority"
    DEPLOY_RESERVE_GROUP = "deploy_reserve_group"
    REPOSITION_GROUP = "reposition_group"
    SET_GROUP_POSTURE = "set_group_posture"
    REINFORCE_CONTROL_POINT = "reinforce_control_point"
    WITHDRAW_GROUP = "withdraw_group"
    HOLD_ACTION = "hold_action"


class GroupPosture(StrEnum):
    DEFENSIVE = "defensive"
    RESERVE = "reserve"
    SCREENING = "screening"
    SUPPORT = "support"
    FALLBACK = "fallback"


class SectorPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DestinationType(StrEnum):
    SECTOR = "sector"
    CONTROL_POINT = "control_point"
    ZONE = "zone"


class ValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIALLY_ACCEPTED = "partially_accepted"


class RejectionCode(StrEnum):
    UNKNOWN_ACTION_TYPE = "unknown_action_type"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_FIELD_VALUE = "invalid_field_value"
    COALITION_MISMATCH = "coalition_mismatch"
    UNKNOWN_ENTITY = "unknown_entity"
    ENTITY_NOT_OWNED = "entity_not_owned"
    ENTITY_UNAVAILABLE = "entity_unavailable"
    DESTINATION_INVALID = "destination_invalid"
    DESTINATION_RESTRICTED = "destination_restricted"
    INSUFFICIENT_BUDGET = "insufficient_budget"
    RESERVE_NOT_AVAILABLE = "reserve_not_available"
    ACTION_CONFLICT = "action_conflict"
    SCENARIO_RULE_VIOLATION = "scenario_rule_violation"
    EXECUTION_NOT_SUPPORTED = "execution_not_supported"


class ModelHostingMode(StrEnum):
    LOCAL = "local"
    LAN = "lan"
    HOSTED = "hosted"


class LoggingFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
