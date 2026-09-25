"""Workstation view models (owned by ui.desktop).

Pure UI state and projections over the gateway - no Tk imports, fully
testable headless. The model NEVER mutates domain state: selecting an
object changes UI context only; every privileged action is a dispatch
request whose receipt carries the real engine states.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from ui.desktop.contracts import (
    CommandSpec,
    Freshness,
    Notification,
    NotificationSeverity,
    PanelDockState,
    PanelState,
    UIContext,
    ViewContextKind,
    ViewState,
    WhySystem,
    WorkspaceState,
    WORKSPACE_PRESETS,
    default_workspace,
)

#: SECTION 7: primary navigation
NAVIGATION = ("Overview", "Markets", "Intelligence", "Portfolio",
              "Execution", "Research", "Strategies", "Risk", "Operations")
OPERATIONS_TABS = ("Orders", "Positions", "Ledger", "Audit", "Governance",
                   "Incidents", "System")

#: SECTION 12: blotter tabs
BLOTTER_TABS = ("Orders", "Fills", "Positions", "Signals", "Events",
                "Decisions", "Logs")

#: SECTION 30: presets map workspace id -> region emphasis
PRESET_PANELS = {
    "OVERVIEW": {"B": ("market-summary",), "C": ("symbol",),
                 "D": ("activity",), "E": ("intelligence",),
                 "F": ("risk",), "G": ("actions",)},
    "MARKET": {"B": ("chart", "market-data", "market-state"),
               "C": ("symbol",), "D": ("activity",),
               "E": ("intelligence",), "F": ("risk",),
               "G": ("actions",)},
    "EXECUTION": {"B": ("orders",), "C": ("order",), "D": ("fills",),
                  "E": ("decision-why",), "F": ("risk",), "G": ("actions",)},
    "RESEARCH": {"B": ("research",), "C": ("research-run",),
                 "D": ("activity",), "E": ("intelligence",),
                 "F": ("risk",), "G": ("actions",)},
    "INTELLIGENCE": {"B": ("intelligence",), "C": ("proposal",),
                     "D": ("activity",), "E": ("evidence",),
                     "F": ("risk",), "G": ("actions",)},
    "RISK": {"B": ("risk",), "C": ("risk-decision",), "D": ("activity",),
             "E": ("intelligence",), "F": ("exposure",), "G": ("actions",)},
    "OPERATIONS": {"B": ("operations",), "C": ("audit-record",),
                   "D": ("activity",), "E": ("intelligence",),
                   "F": ("risk",), "G": ("actions",)},
}


class WorkstationError(ContractError):
    rule_id = "GUI-MODEL"


@dataclass
class ContextStack:
    """SECTION 11: current/previous/related context with back/forward."""
    back: list[UIContext] = field(default_factory=list)
    forward: list[UIContext] = field(default_factory=list)
    current: UIContext | None = None

    def select(self, context: UIContext) -> UIContext:
        context.validate()
        if self.current is not None:
            self.back.append(self.current)
        self.forward.clear()
        self.current = context
        return context

    def go_back(self) -> UIContext | None:
        if not self.back:
            return self.current
        if self.current is not None:
            self.forward.append(self.current)
        self.current = self.back.pop()
        return self.current

    def go_forward(self) -> UIContext | None:
        if not self.forward:
            return self.current
        if self.current is not None:
            self.back.append(self.current)
        self.current = self.forward.pop()
        return self.current

    @property
    def history(self) -> list[str]:
        trail = [c.object_id for c in self.back]
        if self.current:
            trail.append(self.current.object_id)
        return trail


@dataclass
class BlotterFilter:
    """SECTION 13: GLOBAL CONTEXT vs LOCAL VIEW FILTER - the model keeps
    them distinct and never silently overwrites local edits."""
    tab: str = "Orders"
    global_symbol: str | None = None
    local_symbol: str | None = None
    local_override: bool = False
    query: str = ""

    @property
    def effective_symbol(self) -> str | None:
        if self.local_override:
            return self.local_symbol
        return self.global_symbol

    def bind_global(self, symbol: str | None) -> None:
        """Global context change: only applies when no local override."""
        self.global_symbol = symbol
        if not self.local_override:
            self.local_symbol = symbol

    def set_local(self, symbol: str | None) -> None:
        self.local_symbol = symbol
        self.local_override = symbol != self.global_symbol

    def clear_local(self) -> None:
        self.local_symbol = self.global_symbol
        self.local_override = False


class VirtualWindow:
    """SECTION 12: bounded rendering window over large lists."""

    def __init__(self, page_size: int = 50) -> None:
        self.page_size = page_size
        self.offset = 0

    def view(self, rows: list) -> list:
        return rows[self.offset:self.offset + self.page_size]

    def next(self, rows: list) -> list:
        if self.offset + self.page_size < len(rows):
            self.offset += self.page_size
        return self.view(rows)

    def prev(self) -> list:
        self.offset = max(0, self.offset - self.page_size)
        return None  # caller re-reads via view()

    def reset(self) -> None:
        self.offset = 0


@dataclass
class WorkstationModel:
    """The ONE state container behind the A-G shell."""

    def __init__(self, gateway) -> None:
        self._gateway = gateway
        self.state = ViewState.INITIALIZING
        self.environment = gateway.environment
        self.navigation = NAVIGATION[0]
        self.context_stack = ContextStack()
        self.workspace = default_workspace()
        self.blotter_filter = BlotterFilter()
        self.blotter_window = VirtualWindow()
        self.notifications: list[Notification] = []
        self.action_states: dict[str, str] = {}
        self.session: Mapping[str, Any] | None = None
        self.why_system: WhySystem | None = None
        self._listeners: list[Callable[[str], None]] = []

    # ---------------- lifecycle (SECTION 33/60) ---------------- #
    def start(self) -> Mapping[str, Any]:
        self.state = ViewState.LOADING
        try:
            overview = self._gateway.overview()
        except ContractError:
            self.state = ViewState.ERROR
            raise
        self.state = ViewState.READY
        return overview

    def login(self, persona: str, secret: str) -> Mapping[str, Any]:
        info = self._gateway.login(persona, secret)
        self.session = info
        self.state = ViewState.READY
        return info

    def logout(self) -> None:
        self._gateway.logout()
        self.session = None
        self.state = ViewState.INITIALIZING

    # ---------------- context (SECTION 10/11) ---------------- #
    def select_symbol(self, symbol: str) -> UIContext:
        context = UIContext(kind=ViewContextKind.SYMBOL, object_id=symbol,
                            environment=self.environment, symbol=symbol,
                            label=symbol)
        self.context_stack.select(context)
        self.blotter_filter.bind_global(symbol)
        return context

    def select_object(self, kind: ViewContextKind, object_id: str,
                      label: str | None = None) -> UIContext:
        context = UIContext(kind=kind, object_id=object_id,
                            environment=self.environment, label=label)
        self.context_stack.select(context)
        return context

    # ---------------- workspace (SECTION 30/31) ---------------- #
    def apply_preset(self, preset: str) -> WorkspaceState:
        if preset not in WORKSPACE_PRESETS:
            raise WorkstationError(
                f"unknown preset {preset}", location="model.preset",
                rule_id="GUI-LAYOUT")
        panels = []
        for region, panel_ids in PRESET_PANELS[preset].items():
            for index, panel_id in enumerate(panel_ids):
                panels.append(PanelState(panel_id=panel_id, region=region,
                                         order_index=index))
        workspace = WorkspaceState(workspace_id=f"preset-{preset}",
                                   preset=preset, panels=tuple(panels))
        workspace.validate()
        self.workspace = workspace
        return workspace

    def dock_panel(self, panel_id: str, dock: PanelDockState) -> None:
        updated = []
        for panel in self.workspace.panels:
            if panel.panel_id == panel_id:
                panel = PanelState(
                    panel_id=panel.panel_id, region=panel.region,
                    dock=dock, size_weight=panel.size_weight,
                    order_index=panel.order_index, visible=panel.visible)
            updated.append(panel)
        workspace = WorkspaceState(
            workspace_id=self.workspace.workspace_id,
            preset=self.workspace.preset, panels=tuple(updated),
            monitor=self.workspace.monitor)
        workspace.validate()
        self.workspace = workspace

    def restore_workspace(self, persisted: Mapping[str, Any] | None) \
            -> WorkspaceState:
        """SECTION 31: corrupted state recovers to the default layout."""
        if persisted is None:
            self.workspace = default_workspace()
            return self.workspace
        try:
            panels = tuple(
                PanelState(panel_id=p["panel_id"], region=p["region"],
                           dock=PanelDockState(p.get("dock", "DOCKED")),
                           size_weight=float(p.get("size_weight", 1.0)),
                           order_index=int(p.get("order_index", 0)),
                           visible=bool(p.get("visible", True)))
                for p in persisted.get("panels", []))
            workspace = WorkspaceState(
                workspace_id=str(persisted.get("workspace_id", "restored")),
                preset=persisted.get("preset", "OVERVIEW"), panels=panels,
                monitor=int(persisted.get("monitor", 0)))
            workspace.validate()
            if workspace.preset not in WORKSPACE_PRESETS:
                raise WorkstationError("bad preset")
            self.workspace = workspace
            return workspace
        except (ContractError, KeyError, ValueError, TypeError):
            self.workspace = default_workspace()
            return self.workspace

    def persist_workspace(self) -> Mapping[str, Any]:
        return {
            "workspace_id": self.workspace.workspace_id,
            "preset": self.workspace.preset,
            "monitor": self.workspace.monitor,
            "panels": [{
                "panel_id": p.panel_id, "region": p.region,
                "dock": p.dock.value, "size_weight": p.size_weight,
                "order_index": p.order_index, "visible": p.visible,
            } for p in self.workspace.panels],
        }

    # ---------------- data panes ---------------- #
    def market_view(self, symbol: str) -> Mapping[str, Any]:
        return self._gateway.market(symbol)

    def intelligence(self, symbol: str) -> Mapping[str, Any]:
        view = self._gateway.intelligence_view(symbol)
        self.why_system = WhySystem.AI_WHY
        return view

    def core_why(self, order_id: str) -> Mapping[str, Any]:
        view = self._gateway.core_why(order_id)
        self.why_system = WhySystem.CORE_WHY
        return view

    def risk(self) -> Mapping[str, Any]:
        return self._gateway.risk_snapshot()

    def blotter_rows(self) -> list[Mapping[str, Any]]:
        rows = self._gateway.orders()
        symbol = self.blotter_filter.effective_symbol
        if symbol:
            rows = [r for r in rows if r["symbol"] == symbol]
        if self.blotter_filter.query:
            needle = self.blotter_filter.query.lower()
            rows = [r for r in rows
                    if needle in r["order_id"].lower()
                    or needle in r["symbol"].lower()]
        return rows

    def blotter_page(self) -> list[Mapping[str, Any]]:
        return self.blotter_window.view(self.blotter_rows())

    # ---------------- attention + notifications (SECTION 19/54) ------ #
    def refresh_attention(self) -> list[Mapping[str, Any]]:
        items = self._gateway.attention()
        for item in items:
            self.push_notification(
                category=item["category"], title=item["title"],
                severity=NotificationSeverity(
                    item.get("severity", "INFO")))
        return items

    def push_notification(self, *, category: str, title: str,
                          severity: NotificationSeverity | None = None,
                          object_ref: str | None = None) -> Notification:
        notification = Notification(
            notification_id=new_identifier("correlation_id"),
            severity=severity or NotificationSeverity.INFO,
            category=category, title=title,
            timestamp=utc_now(),
            object_ref=object_ref)
        notification.validate()
        self.notifications.insert(0, notification)
        del self.notifications[100:]  # bounded
        return notification

    def unread_count(self) -> int:
        return sum(1 for n in self.notifications if not n.read)

    def mark_all_read(self) -> None:
        self.notifications = [
            Notification(**{**n.__dict__, "read": True})
            for n in self.notifications]

    # ---------------- search + palette (SECTION 20/21) --------------- #
    def search(self, query: str) -> list[Mapping[str, Any]]:
        return self._gateway.search(query)

    def open_search_result(self, result: Mapping[str, Any]) -> UIContext:
        kind_map = {"ORDER": ViewContextKind.ORDER,
                    "POSITION": ViewContextKind.POSITION,
                    "SYMBOL": ViewContextKind.SYMBOL}
        kind = kind_map.get(str(result.get("kind")))
        if kind is None:
            raise WorkstationError(
                f"unsupported search result kind {result.get('kind')}",
                location="model.search", rule_id="GUI-SEARCH")
        return self.select_object(kind, str(result["object_id"]),
                                  label=str(result.get("label")))

    def commands(self) -> list[CommandSpec]:
        specs = [
            CommandSpec(command_id="open.overview", title="Open Overview"),
            CommandSpec(command_id="open.markets", title="Open Markets"),
            CommandSpec(command_id="open.risk", title="Open Risk"),
            CommandSpec(command_id="open.audit", title="Open Audit"),
            CommandSpec(command_id="nav.back", title="Back"),
            CommandSpec(command_id="pause", title="Pause",
                        dangerous=True, requires_permission="PAUSE"),
            CommandSpec(command_id="close_only", title="Close Only",
                        dangerous=True, requires_permission="CLOSE_ONLY"),
            CommandSpec(command_id="emergency_stop", title="Emergency Stop",
                        dangerous=True,
                        requires_permission="EMERGENCY_STOP"),
        ]
        for spec in specs:
            spec.validate()
        return specs

    def run_command(self, command_id: str,
                    confirmed: bool = False) -> Mapping[str, Any]:
        """SECTION 21: dangerous commands need explicit confirmation; no
        command bypasses authorization (the gateway enforces it)."""
        spec = next((c for c in self.commands()
                     if c.command_id == command_id), None)
        if spec is None:
            return {"state": "REJECTED", "reasons": ("unknown command",)}
        if spec.dangerous and not confirmed:
            return {"state": "REJECTED",
                    "reasons": ("confirmation required",)}
        if command_id.startswith("open."):
            self.navigation = command_id.split(".", 1)[1].capitalize()
            return {"state": "APPLIED", "reasons": ("workspace opened",)}
        if command_id == "nav.back":
            self.context_stack.go_back()
            return {"state": "APPLIED", "reasons": ("context back",)}
        action = {"pause": "pause", "close_only": "close_only",
                  "emergency_stop": "emergency_stop"}.get(command_id)
        if action:
            try:
                receipt = self._gateway.act(
                    action, {"reason": "command palette"})
            except ContractError as error:
                self.action_states[command_id] = "REJECTED"
                return {"state": "REJECTED", "reasons": (str(error),)}
            self.action_states[command_id] = receipt.state
            return {"state": receipt.state,
                    "reasons": receipt.reasons}
        return {"state": "UNKNOWN", "reasons": ("unhandled",)}

    # ---------------- actions (SECTION 45) ---------------- #
    def submit_order(self, symbol: str, side: str, quantity: str) \
            -> Mapping[str, Any]:
        key = f"order:{symbol}:{side}:{quantity}"
        self.action_states[key] = "REQUESTED"
        try:
            receipt = self._gateway.act(
                "submit_order", {"symbol": symbol, "side": side,
                                 "quantity": quantity})
        except ContractError as error:
            self.action_states[key] = "REJECTED"
            self.push_notification(
                category="EXECUTION",
                title=f"order REJECTED ({symbol} {side} {quantity})",
                severity=NotificationSeverity.WARNING)
            return {"state": "REJECTED", "reasons": (str(error),),
                    "order_id": None, "position_id": None}
        self.action_states[key] = receipt.state
        self.push_notification(
            category="EXECUTION",
            title=f"order {receipt.state} ({symbol} {side} {quantity})",
            severity=NotificationSeverity.INFO
            if receipt.state == "APPLIED" else NotificationSeverity.WARNING,
            object_ref=receipt.order_id)
        return {"state": receipt.state, "reasons": receipt.reasons,
                "order_id": receipt.order_id,
                "position_id": receipt.position_id}

    # ---------------- disconnect (SECTION 46) ---------------- #
    def connection_state(self) -> Freshness:
        status = self._gateway.freshness()["status"]
        return Freshness(status)

    def disconnect(self) -> None:
        self._gateway.disconnect()
        self.state = ViewState.DISCONNECTED

    def reconnect(self) -> None:
        self._gateway.reconnect()
        self.state = ViewState.READY
