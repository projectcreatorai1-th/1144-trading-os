"""Desktop UI contracts (owned by ui.desktop).

Presentation-layer contracts ONLY (SECTION 6): UI context, workspace
state, view states, freshness, notifications, actions, commands.

None of these are authoritative for trading, risk, authorization,
governance or audit. Context selection NEVER mutates domain state
(SECTION 10); workspace persistence is UI-only and recovers safely
(SECTION 31); UNKNOWN is a first-class state everywhere (SECTION 33).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.0.0"


class ViewContextKind(Enum):
    SYMBOL = "SYMBOL"
    MARKET_STATE = "MARKET_STATE"
    SIGNAL = "SIGNAL"
    AI_PROPOSAL = "AI_PROPOSAL"
    STRATEGY = "STRATEGY"
    POSITION = "POSITION"
    ORDER = "ORDER"
    FILL = "FILL"
    DECISION = "DECISION"
    RISK_DECISION = "RISK_DECISION"
    RESEARCH_RUN = "RESEARCH_RUN"
    BACKTEST = "BACKTEST"
    REPLAY = "REPLAY"
    EVENT = "EVENT"
    LEDGER_ENTRY = "LEDGER_ENTRY"
    AUDIT_RECORD = "AUDIT_RECORD"
    GOVERNANCE_ITEM = "GOVERNANCE_ITEM"
    INCIDENT = "INCIDENT"


class ViewState(Enum):
    """SECTION 33: explicit UI states - never just loading/not-loading."""
    INITIALIZING = "INITIALIZING"
    LOADING = "LOADING"
    READY = "READY"
    EMPTY = "EMPTY"
    STALE = "STALE"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"
    PERMISSION_DENIED = "PERMISSION_DENIED"


class Freshness(Enum):
    """SECTION 27: never show stale data as current."""
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    DISCONNECTED = "DISCONNECTED"


class TimeKind(Enum):
    """SECTION 26: explicit time semantics; live and historical never mix."""
    EVENT_TIME = "EVENT_TIME"
    RECEIVED_TIME = "RECEIVED_TIME"
    PROCESSED_TIME = "PROCESSED_TIME"
    EXECUTION_TIME = "EXECUTION_TIME"
    AUDIT_TIME = "AUDIT_TIME"
    REPLAY_TIME = "REPLAY_TIME"


class WhySystem(Enum):
    """SECTION 16: two DIFFERENT WHY systems - never merged."""
    AI_WHY = "AI_WHY"
    CORE_WHY = "CORE_WHY"


class ActionState(Enum):
    """SECTION 45: button clicked is never success."""
    REQUESTED = "REQUESTED"
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class NotificationSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class UIContext:
    """The canonical UI context (SECTION 10). Selecting an object is a
    pure UI fact - it can never mutate domain state."""
    kind: ViewContextKind
    object_id: str
    environment: str
    symbol: str | None = None
    label: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.kind, ViewContextKind):
            raise ContractValidationError(
                "context.kind must be a ViewContextKind",
                location="uicontext.kind", rule_id="SCHEMA-ENUM")
        if not isinstance(self.object_id, str) or not self.object_id:
            raise ContractValidationError(
                "context.object_id must be a non-empty string",
                location="uicontext.object_id")
        if not isinstance(self.environment, str) or not self.environment:
            raise ContractValidationError(
                "context.environment must be explicit (SIMULATION/DEMO/"
                "LIVE are never interchangeable)",
                location="uicontext.environment", rule_id="GUI-ENV")


class PanelDockState(Enum):
    DOCKED = "DOCKED"
    FLOATING = "FLOATING"
    PINNED = "PINNED"
    COLLAPSED = "COLLAPSED"


@dataclass(frozen=True)
class PanelState:
    panel_id: str
    region: str  # A..G
    dock: PanelDockState = PanelDockState.DOCKED
    size_weight: float = 1.0
    order_index: int = 0
    visible: bool = True
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.panel_id, str) or not self.panel_id:
            raise ContractValidationError(
                "panel.panel_id must be non-empty",
                location="panel.panel_id")
        if self.region not in ("A", "B", "C", "D", "E", "F", "G"):
            raise ContractValidationError(
                "panel.region must be one of the A-G workstation regions",
                location="panel.region", rule_id="GUI-LAYOUT")
        if not isinstance(self.dock, PanelDockState):
            raise ContractValidationError(
                "panel.dock must be a PanelDockState",
                location="panel.dock", rule_id="SCHEMA-ENUM")
        if not 0.0 < float(self.size_weight) <= 100.0:
            raise ContractValidationError(
                "panel.size_weight must be within (0, 100]",
                location="panel.size_weight")
        if self.order_index < 0:
            raise ContractValidationError(
                "panel.order_index must be >= 0",
                location="panel.order_index")


@dataclass(frozen=True)
class WorkspaceState:
    """Persistable UI-only workspace (SECTION 31). Corrupted state must
    recover safely to a known valid layout - validate() is strict, and
    the loader falls back to presets."""
    workspace_id: str
    preset: str
    panels: tuple[PanelState, ...]
    monitor: int = 0
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.workspace_id, str) or not self.workspace_id:
            raise ContractValidationError(
                "workspace.workspace_id must be non-empty",
                location="workspace.workspace_id")
        from ui.desktop.contracts import WORKSPACE_PRESETS
        if self.preset not in WORKSPACE_PRESETS:
            raise ContractValidationError(
                f"workspace.preset must be one of {sorted(WORKSPACE_PRESETS)}",
                location="workspace.preset", rule_id="GUI-LAYOUT")
        if not self.panels:
            raise ContractValidationError(
                "workspace.panels must be non-empty",
                location="workspace.panels")
        for panel in self.panels:
            panel.validate()


WORKSPACE_PRESETS = ("OVERVIEW", "MARKET", "EXECUTION", "RESEARCH",
                     "INTELLIGENCE", "RISK", "OPERATIONS")

#: The canonical A-G layout every preset derives from (SECTION 1).
REGIONS = ("A", "B", "C", "D", "E", "F", "G")


def default_workspace(preset: str = "OVERVIEW") -> WorkspaceState:
    """The known-valid fallback layout (SECTION 31 recovery)."""
    panels = tuple(
        PanelState(panel_id=f"{region}-main", region=region,
                   order_index=index)
        for index, region in enumerate(REGIONS))
    workspace = WorkspaceState(workspace_id="default", preset=preset,
                               panels=panels)
    workspace.validate()
    return workspace


@dataclass(frozen=True)
class Notification:
    notification_id: str
    severity: NotificationSeverity
    category: str
    title: str
    timestamp: datetime
    object_ref: str | None = None
    read: bool = False
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.notification_id, str) or not self.notification_id:
            raise ContractValidationError(
                "notification.notification_id must be non-empty",
                location="notification.notification_id")
        if not isinstance(self.severity, NotificationSeverity):
            raise ContractValidationError(
                "notification.severity must be a NotificationSeverity",
                location="notification.severity", rule_id="SCHEMA-ENUM")
        for name in ("category", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"notification.{name} must be a non-empty string",
                    location=f"notification.{name}")
        ensure_utc(self.timestamp, location="notification.timestamp")


@dataclass(frozen=True)
class CommandSpec:
    """SECTION 21: Ctrl+K commands. Dangerous commands require explicit
    confirmation; no command bypasses authorization."""
    command_id: str
    title: str
    dangerous: bool = False
    requires_permission: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        for name in ("command_id", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContractValidationError(
                    f"command.{name} must be a non-empty string",
                    location=f"command.{name}")
