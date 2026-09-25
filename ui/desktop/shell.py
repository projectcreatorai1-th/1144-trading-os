"""A-G workstation shell (owned by ui.desktop, tkinter).

Layout per the Phase 9 source of truth:

top bar | A nav | B workspace | C inspector
        | D blotter (full width)
        | E intelligence | F portfolio/risk
        | G action/approval/attention (full width)

The shell renders whatever the WorkstationModel projects; it holds no
authority. Ctrl+K palette, global search, keyboard navigation, explicit
view states and environment identification included.

BASELINE REPAIR (2026-09-24, from the GUI diagnosis):
- navigation now applies the mapped preset and visibly switches the
  B workspace (title/tab/content per navigation) — RC-F fix
- protected actions surface REJECTED receipts on a visible banner
  (presentation only; the viewmodel/safety semantics are untouched) —
  silent-block fix
"""
from __future__ import annotations

from typing import Mapping

import tkinter as tk
from tkinter import simpledialog, ttk

from architecture.contracts.errors import ContractError

from ui.desktop.contracts import PanelDockState, ViewState
from ui.desktop.viewmodels import (
    NAVIGATION,
    WorkstationModel,
)

PALETTE_SHORTCUT = "<Control-k>"
SEARCH_SHORTCUT = "<Control-f>"

#: Deterministic navigation -> preset mapping (reuse of the existing
#: preset system; Portfolio/Strategy share the closest existing preset).
NAV_PRESET = {
    "Overview": "OVERVIEW",
    "Markets": "MARKET",
    "Intelligence": "INTELLIGENCE",
    "Portfolio": "RISK",
    "Execution": "EXECUTION",
    "Research": "RESEARCH",
    "Strategies": "RESEARCH",
    "Risk": "RISK",
    "Operations": "OPERATIONS",
}


class LoginDialog(tk.Toplevel):
    """SECTION 37: login through Phase 8 authentication (persona table;
    secrets are never stored by the GUI)."""

    def __init__(self, parent, model: WorkstationModel):
        super().__init__(parent)
        self.title("1144 Trading OS - Login")
        self.resizable(False, False)
        self.result = None
        self.model = model
        ttk.Label(self, text="Persona (trader / risk_manager)").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.persona = ttk.Entry(self)
        self.persona.insert(0, "trader")
        self.persona.grid(row=0, column=1, padx=8, pady=4)
        ttk.Label(self, text="Secret").grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        self.secret = ttk.Entry(self, show="*")
        self.secret.grid(row=1, column=1, padx=8, pady=4)
        ttk.Button(self, text="Login", command=self._submit).grid(
            row=2, column=0, columnspan=2, pady=8)
        self.bind("<Return>", lambda _e: self._submit())
        self.secret.focus_set()

    def _submit(self) -> None:
        try:
            self.result = self.model.login(
                self.persona.get().strip(), self.secret.get())
        except ContractError:
            self.result = None
        self.destroy()


class CommandPalette(tk.Toplevel):
    """SECTION 21: Ctrl+K. Dangerous commands require confirmation."""

    def __init__(self, parent, model: WorkstationModel):
        super().__init__(parent)
        self.title("Command Palette")
        self.model = model
        self.entry = ttk.Entry(self)
        self.entry.pack(fill="x", padx=8, pady=8)
        self.entry.focus_set()
        self.listbox = tk.Listbox(self, height=10)
        self.listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.commands = model.commands()
        for spec in self.commands:
            self.listbox.insert(
                "end", f"{spec.title}"
                       f"{'  [CONFIRM]' if spec.dangerous else ''}")
        self.entry.bind("<KeyRelease>", self._filter)
        self.listbox.bind("<Double-1>", self._run)
        self.bind("<Return>", self._run)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _filter(self, _event) -> None:
        needle = self.entry.get().lower()
        self.listbox.delete(0, "end")
        for spec in self.commands:
            if needle in spec.title.lower():
                self.listbox.insert(
                    "end", f"{spec.title}"
                           f"{'  [CONFIRM]' if spec.dangerous else ''}")

    def _run(self, _event=None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        title = self.listbox.get(selection[0]).replace("  [CONFIRM]", "")
        spec = next((c for c in self.commands if c.title == title), None)
        if spec is None:
            return
        confirmed = True
        if spec.dangerous:
            from tkinter import messagebox
            confirmed = messagebox.askyesno(
                "Confirm", f"Execute dangerous command '{spec.title}'?")
        result = self.model.run_command(spec.command_id,
                                        confirmed=confirmed)
        if result.get("state") != "APPLIED":
            from tkinter import messagebox
            messagebox.showwarning(
                "Command", f"{result['state']}: {result['reasons'][0]}")
        self.destroy()


class Shell(tk.Tk):
    """The ONE workstation window (A-G)."""

    def __init__(self, model: WorkstationModel):
        super().__init__()
        self.model = model
        self.title("1144 Trading OS")
        self.geometry("1280x800")
        self._build_top_bar()
        self._build_body()
        self._build_status_bar()
        self.bind(PALETTE_SHORTCUT, self._open_palette)
        self.bind(SEARCH_SHORTCUT, self._focus_search)
        self.bind("<Escape>", self._close_overlays)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.after(2000, self._periodic_refresh)

    # ---------------- top bar ---------------- #
    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.env_label = ttk.Label(bar, text="ENV: ...", font=("", 10, "bold"))
        self.env_label.pack(side="left", padx=8)
        self.status_label = ttk.Label(bar, text="INITIALIZING")
        self.status_label.pack(side="left", padx=8)
        self.conn_label = ttk.Label(bar, text="DATA ?")
        self.conn_label.pack(side="left", padx=8)
        self.search_entry = ttk.Entry(bar, width=32)
        self.search_entry.pack(side="right", padx=8)
        self.search_entry.bind("<Return>", self._run_search)
        ttk.Label(bar, text="Search").pack(side="right")
        self.notif_label = ttk.Label(bar, text="bell 0")
        self.notif_label.pack(side="right", padx=8)
        ttk.Label(bar, text="Ctrl+K").pack(side="right", padx=8)

    # ---------------- A-G body ---------------- #
    def _build_body(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        # A - navigation
        nav = ttk.Frame(container, width=160)
        nav.pack(side="left", fill="y")
        ttk.Label(nav, text="NAVIGATION").pack(anchor="w", padx=6, pady=4)
        self.nav_buttons = {}
        for item in NAVIGATION:
            button = ttk.Button(nav, text=item,
                                command=lambda i=item: self._navigate(i))
            button.pack(fill="x", padx=6, pady=1)
            self.nav_buttons[item] = button

        # center column: B + D + E/F + G
        center = ttk.Frame(container)
        center.pack(side="left", fill="both", expand=True)

        # B - main workspace (title + notebook; the title makes the
        # active workspace visible on every navigation)
        self.b_title = ttk.Label(
            center, text="B - Workspace", font=("", 11, "bold"))
        self.b_title.pack(anchor="w", padx=6)
        self.b_notebook = ttk.Notebook(center)
        self.b_notebook.pack(fill="both", expand=True, padx=4, pady=4)
        self.b_market = ttk.Treeview(self.b_notebook, columns=("time", "close"),
                                      show="headings", height=10)
        self.b_market.heading("time", text="Event Time")
        self.b_market.heading("close", text="Close (SYNTHETIC)")
        self.b_notebook.add(self.b_market, text="Market")
        self.b_text = tk.Text(self.b_notebook, height=10, state="disabled")
        self.b_notebook.add(self.b_text, text="Workspace")

        # D - blotter
        blotter = ttk.LabelFrame(center, text="D - Activity / Blotter")
        blotter.pack(fill="x", padx=4, pady=4)
        self.blotter = ttk.Treeview(
            blotter, columns=("id", "symbol", "side", "qty", "status"),
            show="headings", height=6)
        for col, title in (("id", "Order"), ("symbol", "Symbol"),
                           ("side", "Side"), ("qty", "Qty"),
                           ("status", "Status")):
            self.blotter.heading(col, text=title)
        self.blotter.pack(fill="x", padx=4, pady=4)

        # E + F
        ef = ttk.Frame(center)
        ef.pack(fill="x", padx=4, pady=4)
        self.e_text = self._pane(ef, "E - Intelligence / Decision (AI WHY)")
        self.f_text = self._pane(ef, "F - Portfolio / Risk")

        # G - actions (+ visible action-result banner)
        actions = ttk.LabelFrame(center, text="G - Action / Approval")
        actions.pack(fill="x", padx=4, pady=4)
        self.banner = ttk.Label(actions, text="", anchor="w",
                                foreground="#b00020")
        self.banner.pack(fill="x", padx=4)
        for label, command in (
                ("Buy 0.1", lambda: self.model.submit_order(
                    self._current_symbol() or "EURUSD", "BUY", "0.1")),
                ("Pause", lambda: self.model.run_command("pause",
                                                         confirmed=True)),
                ("Close Only", lambda: self.model.run_command(
                    "close_only", confirmed=True)),
                ("Emergency Stop", lambda: self.model.run_command(
                    "emergency_stop", confirmed=True))):
            ttk.Button(actions, text=label,
                       command=lambda c=command, l=label: self._guarded(
                           c, action_label=l)).pack(
                side="left", padx=6, pady=4)
        self.g_text = tk.Text(actions, height=2, state="disabled")
        self.g_text.pack(fill="x", padx=4, pady=2)

        # C - inspector
        inspector = ttk.Frame(container, width=300)
        inspector.pack(side="right", fill="y")
        ttk.Label(inspector, text="C - INSPECTOR").pack(anchor="w",
                                                        padx=6, pady=4)
        self.c_text = tk.Text(inspector, state="disabled")
        self.c_text.pack(fill="both", expand=True, padx=6, pady=4)

    def _pane(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title)
        frame.pack(side="left", fill="both", expand=True, padx=4)
        text = tk.Text(frame, height=5, state="disabled")
        text.pack(fill="both", expand=True, padx=4, pady=4)
        return text

    def _build_status_bar(self) -> None:
        self.statusbar = ttk.Label(self, anchor="w",
                                   text="SIMULATION != DEMO != LIVE")
        self.statusbar.pack(fill="x")

    # ---------------- behaviors ---------------- #
    def _navigate(self, item: str) -> None:
        """BASELINE REPAIR (RC-F): navigation now (1) sets model state,
        (2) applies the mapped preset through the EXISTING preset system,
        (3) selects the matching B tab and (4) renders the workspace —
        every navigation produces a visible change."""
        self.model.navigation = item
        try:
            self.model.apply_preset(NAV_PRESET[item])
        except ContractError:
            pass  # preset mapping is total; defensive only
        self._select_workspace_tab(item)
        self._render()

    def _select_workspace_tab(self, item: str) -> None:
        # Markets shows the market table; every other workspace shows
        # its own content tab
        target = self.b_market if item == "Markets" else self.b_text
        try:
            self.b_notebook.select(target)
        except tk.TclError:
            pass

    def _workspace_content(self, item: str) -> str:
        """REAL data per workspace (gateway/model projections only;
        honest empty states — no fabricated content)."""
        model = self.model
        gateway = model._gateway
        header = f"{item.upper()}  |  preset {model.workspace.preset}"
        try:
            if item == "Overview":
                overview = gateway.overview()
                return header + (
                    f"\nenvironment: {overview['environment']}"
                    f"\nsession: {model.session['role'] if model.session else 'NOT LOGGED IN'}"
                    f"\nrisk state: {overview['risk_state']}"
                    f"\nfeed: {overview['freshness']['status']}"
                    f"\norders: {len(gateway.orders())}"
                    f"\npositions: {len(gateway.positions())}")
            if item == "Markets":
                rows = gateway.watchlist()
                return header + "\n" + "\n".join(
                    f"{r['symbol']:8s} last {r['last']}"
                    f"  chg {r['change']:+}  ({r['source']})" for r in rows)
            if item == "Intelligence":
                symbol = self._current_symbol()
                if symbol is None:
                    return header + "\nno symbol selected - select a symbol"
                view = model.intelligence(symbol)
                return header + (
                    f"\n{symbol} ADVISORY ONLY"
                    f"\nlabel {view['label']}  p={view['probability']}"
                    f"\nWHY (AI): " + " -> ".join(view["why"]["chain"]))
            if item == "Portfolio":
                positions = gateway.positions()
                ledger = gateway.ledger_entries(5)
                return header + (
                    f"\npositions: {len(positions)}"
                    + "".join(f"\n  {p['symbol']} {p['quantity']}"
                              for p in positions[:5])
                    + f"\nledger entries: {len(ledger)}")
            if item == "Execution":
                orders = gateway.orders()
                return header + (
                    f"\norders: {len(orders)}"
                    + "".join(f"\n  {o['order_id'][:12]} {o['symbol']} "
                              f"{o['side']} {o['quantity']} {o['status']}"
                              for o in orders[:5]))
            if item in ("Research", "Strategies"):
                context = model.context_stack.current
                return header + (
                    f"\nstrategy id: {gateway.strategy_id[:20]}..."
                    f"\nresearch runs: 0 (none executed this session)"
                    f"\ncontext: "
                    f"{context.kind.value if context else 'none'}")
            if item == "Risk":
                risk = model.risk()
                return header + (
                    f"\nrisk state: {risk['risk_state']}"
                    f"\ndecision: {risk['decision']}"
                    f"\ngross: {risk['gross_exposure']}"
                    f"\nlimits: {risk['limits']}")
            if item == "Operations":
                return header + (
                    f"\nsystem: {model.state.value}"
                    f"\nfeed: {gateway.freshness()['status']}"
                    f"\nnotifications: {len(model.notifications)}")
        except ContractError as error:
            return header + f"\nUNAVAILABLE: {error}"
        return header

    def _current_symbol(self) -> str | None:
        context = self.model.context_stack.current
        if context is None:
            return None
        if context.symbol:
            return context.symbol
        # search establishes a SYMBOL-kind context whose object_id IS the
        # symbol (presentation-layer resolution; canonical context intact)
        from ui.desktop.contracts import ViewContextKind
        if context.kind is ViewContextKind.SYMBOL:
            return context.object_id
        return None

    def _guarded(self, command, action_label: str = "Action") -> None:
        """BASELINE REPAIR (silent-block fix): presentation only —
        safety semantics stay in the viewmodel. A REJECTED receipt now
        shows on the visible banner instead of disappearing."""
        result = None
        try:
            result = command()
        except ContractError as error:
            self._show_rejection(action_label, str(error)[:160])
        else:
            if isinstance(result, Mapping) and \
                    result.get("state") == "REJECTED":
                reasons = result.get("reasons") or ("unknown reason",)
                self._show_rejection(action_label, str(reasons[0])[:160])
            elif isinstance(result, Mapping):
                self.banner.config(
                    text=f"{action_label}: {result.get('state', 'DONE')}",
                    foreground="#006600")
        self._render()

    def _show_rejection(self, action_label: str, reason: str) -> None:
        next_step = ("Log in first (persona: trader / risk_manager)."
                     if self.model.session is None else
                     "Check permission / risk state.")
        self.banner.config(
            text=f"ACTION REJECTED — {action_label}\nReason: {reason}"
                 f"\nNext step: {next_step}",
            foreground="#b00020")
        self.model.push_notification(
            category="ACTION", title=f"{action_label} REJECTED",
            severity=None, object_ref=None)

    def _open_palette(self, _event=None) -> None:
        CommandPalette(self, self.model)

    def _focus_search(self, _event=None) -> None:
        self.search_entry.focus_set()

    def _close_overlays(self, _event=None) -> None:
        return

    def _run_search(self, _event=None) -> None:
        query = self.search_entry.get().strip()
        results = self.model.search(query)
        if results:
            self.model.open_search_result(results[0])
        self._render()

    def _set_text(self, widget, content: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.config(state="disabled")

    def _render(self) -> None:
        model = self.model
        self.env_label.config(text=f"ENV: {model.environment}")
        self.status_label.config(text=model.state.value)
        freshness = model.connection_state().value
        self.conn_label.config(text=f"DATA {freshness}")
        self.notif_label.config(text=f"bell {model.unread_count()}")

        # B workspace title + content reflect the ACTIVE navigation
        item = model.navigation
        self.b_title.config(
            text=f"B - Workspace: {item}   "
                 f"[preset {model.workspace.preset}]")
        self._set_text(self.b_text, self._workspace_content(item))

        context = model.context_stack.current
        inspector = "No selection"
        if context is not None:
            inspector = f"{context.kind.value}: {context.object_id}"
            if context.symbol:
                try:
                    market = model.market_view(context.symbol)
                    inspector += (
                        f"\nlast: {market['latest']} ({market['source']})"
                        f"\nfreshness: {market['freshness']['status']}")
                except ContractError as error:
                    inspector += f"\n{error}"
            inspector += "\nhistory: " + " -> ".join(
                model.context_stack.history[-5:])
        self._set_text(self.c_text, inspector)

        for row in self.blotter.get_children():
            self.blotter.delete(row)
        for row in model.blotter_page():
            self.blotter.insert("", "end", values=(
                row["order_id"][:14], row["symbol"], row["side"],
                row["quantity"], row["status"]))

        symbol = self._current_symbol()
        if symbol:
            try:
                intel = model.intelligence(symbol)
                self._set_text(
                    self.e_text,
                    f"ADVISORY ONLY\n{intel['label']} "
                    f"p={intel['probability']}\nWHY: "
                    + " -> ".join(intel["why"]["chain"]))
            except ContractError as error:
                self._set_text(self.e_text, f"ERROR: {error}")
            try:
                risk = model.risk()
                self._set_text(
                    self.f_text,
                    f"risk state: {risk['risk_state']}\n"
                    f"decision: {risk['decision']}\n"
                    f"gross: {risk['gross_exposure']}\n"
                    f"limits: {risk['limits']}")
            except ContractError as error:
                self._set_text(self.f_text, f"ERROR: {error}")
        self.statusbar.config(
            text=f"{model.navigation} | {model.workspace.preset} | "
                 f"contexts: {len(model.context_stack.history)}")

    def _periodic_refresh(self) -> None:
        try:
            self.model.refresh_attention()
            self._render()
        finally:
            self.after(2000, self._periodic_refresh)

    def run(self) -> None:
        self._render()
        self.mainloop()

    def shutdown(self) -> None:
        """SECTION 61: clean shutdown - no fabricated domain writes."""
        try:
            self.model.logout()
        except ContractError:
            pass
        self.destroy()


