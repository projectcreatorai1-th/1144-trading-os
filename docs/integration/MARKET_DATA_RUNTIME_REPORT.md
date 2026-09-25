# MARKET_DATA_RUNTIME_REPORT.md

Adapter path MT5TickSource→MT5MarketDataAdapter→IngestionPipeline is
the ONLY pipeline (no duplicate). Real-terminal ticks were accepted
2026-09-25 (accepted=1, freshness CURRENT) after the canonical
server-local-epoch fix (+measured offset per tick, ±14h bounds,
non-tz-like skew fails closed FDX-TIME; contract 1.1.0; 15/15 tests
incl. live-terminal case). Duplicate/out-of-order/missing detection:
chaos suite PASS. DEMO-labeled tick evidence: BLOCKED (§3 D/E).
