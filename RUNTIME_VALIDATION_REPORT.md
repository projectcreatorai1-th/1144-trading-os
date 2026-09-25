# RUNTIME_VALIDATION_REPORT.md — real terminal, 2026-09-25

Probe (live): MetaTrader5 5.0.6180; initialize() OK; terminal logged in
account 411173797 @ XMGlobal-MT5 16; trade_mode = 2 =
ACCOUNT_TRADE_MODE_REAL; EURUSD ticks streaming.

Runtime gate rerun (tools/p10_runtime_gate.py, real terminal):
PASS  real_connection (CONNECTED)
PASS  real_ticks_pipeline (accepted=1, freshness CURRENT — epoch fix
      proven on real data)
PASS  long_run_bounded (60 polls / 30 s, peak 0.02 MB)
PASS  safety_pause_blocks (REJECTED while paused)
PASS  disconnect_reconnect_real
REFUSED prerequisites/demo_execution/reconciliation/live_safety:
      the terminal holds a REAL account — the gate fails closed by design.

CONCLUSION: MARKET DATA + CONNECTIVITY + SAFETY = PASS with real
evidence. EXECUTION-chain runtime validation = BLOCKED until a human
switches the terminal login to a DEMO account (terminal-side action;
this system will not touch the account). No LIVE anything, ever, here.
