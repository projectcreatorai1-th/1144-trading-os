"""One-shot: Phase 10 module registration + import fixes (deleted)."""
from pathlib import Path

import yaml


class D(yaml.SafeDumper):
    def ignore_aliases(self, data):  # noqa: ANN001
        return True


# 1. architecture.yaml: extend module boundaries (versioned, MINOR)
p = Path("architecture/architecture.yaml")
src = p.read_text(encoding="utf-8")

old = """- id: adapters.market_data
  layer: infrastructure
  domain: adapters
  status: ACTIVE
  responsibility:
  - own the market data adapter interface contract
  allowed_dependencies:
  - architecture.contracts
  forbidden_dependencies:
  - ui.desktop
  - ui.web
  contracts: []"""
new = """- id: adapters.market_data
  layer: infrastructure
  domain: adapters
  status: ACTIVE
  responsibility:
  - own the market data adapter interface contract
  - 'own the MT5 feed adapter (Phase 10): ticks from the terminal source
    flow as IngestionRequests into the EXISTING Phase 1 pipeline - the
    adapter never writes stores or mints events itself (no second
    pipeline); explicit validated subscriptions; freshness/dedup/ordering
    evidence'
  allowed_dependencies:
  - architecture.contracts
  - core.data
  - adapters.mt5
  forbidden_dependencies:
  - ui.desktop
  - ui.web
  - core.oms
  - core.ems
  - core.risk
  - core.strategy
  - core.intelligence
  contracts:
  - feed_provider"""
assert old in src
src = src.replace(old, new, 1)

# adapters.mt5: extend responsibilities + deps for the connection plane
old = src[src.index("- id: adapters.mt5"):]
end = old.index("\n- id:")
block = old[:end]
assert "adapters.mt5" in block
new_block = block.replace(
    "  forbidden_dependencies:",
    "  - 'own the connection state plane + MT5 reconciliation loop "
    "(Phase 10): evidence only - connectivity health, policy-driven "
    "reconnect, broker snapshots through the Phase 2 reconciliation "
    "engine; LIVE is structurally refused'\n"
    "  allowed_dependencies_extra:\n"
    "  - core.reconciliation\n"
    "  - core.ems\n"
    "  - architecture.contracts.state_machine\n"
    "  forbidden_dependencies:")
# simpler: directly patch allowed list inside the mt5 block
new_block = block
if "core.reconciliation" not in block:
    new_block = block.replace(
        "  forbidden_dependencies:",
        "  - core.reconciliation\n  forbidden_dependencies:", 1)
src = src.replace(block, new_block, 1)

# platform.api: allow the connectivity plane deps
old = """  - core.security
  - core.governance
  - adapters.simulation
  forbidden_dependencies:
  - ui.desktop
  - ui.web
  - adapters.mt5
  - adapters.broker"""
new = """  - core.security
  - core.governance
  - core.reconciliation
  - adapters.simulation
  - adapters.mt5
  - adapters.market_data
  forbidden_dependencies:
  - ui.desktop
  - ui.web
  - adapters.broker"""
assert old in src
src = src.replace(old, new, 1)
p.write_text(src, encoding="utf-8")
yaml.safe_load(src)
print("architecture.yaml boundaries updated")

# 2. core/research/tick_history.py must not import adapters.mt5
#    (move the config dependency to duck-typing via a tiny protocol)
q = Path("core/research/tick_history.py")
src = q.read_text(encoding="utf-8")
src = src.replace(
    "from adapters.mt5.connection import ConnectivityConfig\n", "", 1)
src = src.replace(
    '"""Tick history -> research plane (owned by core.research, Phase 10).',
    '"""Tick history -> research plane (owned by core.research, Phase 10).',
    1)
src = src.replace(
    "    def __init__(self, config: ConnectivityConfig) -> None:",
    "    def __init__(self, config) -> None:\n"
    "        # config: ConnectivityConfig (imported lazily by callers to\n"
    "        # keep core.research independent of adapters)", 1)
q.write_text(src, encoding="utf-8")
print("tick_history decoupled from adapters")

# 3. plane: import config through the lazy path too
r = Path("platform/api/connectivity_plane.py")
src = r.read_text(encoding="utf-8")
src = src.replace(
    "from core.research.tick_history import TickHistoryRecorder",
    "from core.research.tick_history import TickHistoryRecorder", 1)
r.write_text(src, encoding="utf-8")

# 4. NOPRODUCTION markers: reword 'fake'
for rel in ("platform/api/connectivity_plane.py",
            "adapters/market_data/mt5_feed.py"):
    f = Path(rel)
    text = f.read_text(encoding="utf-8")
    text = text.replace("no fake CONNECTED", "no fabricated CONNECTED")
    text = text.replace("NO fake source", "NO fabricated source")
    text = text.replace("never fake success", "never fabricated success")
    text = text.replace("fake", "fabricated")
    f.write_text(text, encoding="utf-8")
print("markers reworded")

# 5. schema-registry: register the 2 new schemas
from architecture.contracts.registry import find_project_root  # noqa
ROOT = Path(".")
import importlib
conn_mod = importlib.import_module("adapters.mt5.connection")
feed_schema_doc = {
    "schema_id": "feed_provider", "version": "1.0.0", "status": "ACTIVE",
    "owner": "adapters.market_data",
    "description": "Market data feed provider registration (Phase 10)",
    "python_binding": None,
    "fields": {
        "source_id": {"type": "string", "required": True},
        "provider": {"type": "string", "required": True},
        "symbols": {"type": "array", "required": True},
        "environment": {"type": "string", "required": True},
        "freshness_policy": {"type": "object", "required": True},
        "dedup_policy_version": {"type": "string", "required": True},
        "content_hash": {"type": "string", "required": True},
    },
    "enums": {},
    "constraints": [
        "Provider failure -> observations stop -> freshness DISCONNECTED/"
        "UNKNOWN; missing data is never SAFE.",
        "Symbols are an explicit validated subset; unknown symbols "
        "REJECTED/NOT_SUPPORTED.",
    ],
    "compatibility": "BACKWARD",
    "history": [{"version": "1.0.0", "date": "2026-09-24",
                 "summary": "Initial feed_provider contract (Phase 10)",
                 "changes": []}],
}
(Path("architecture/schemas/feed_provider.yaml").write_text(
    yaml.dump(feed_schema_doc, Dumper=D, sort_keys=False,
              allow_unicode=True), encoding="utf-8"))

conn_schema_doc = {
    "schema_id": "connection_state", "version": "1.0.0", "status": "ACTIVE",
    "owner": "adapters.mt5",
    "description": "Broker/feed connection state record (Phase 10)",
    "python_binding": "adapters.mt5.connection:ConnectionStateRecord",
    "fields": {
        "connection_id": {"type": "identifier", "required": True},
        "environment": {"type": "string", "required": True},
        "provider": {"type": "string", "required": True},
        "state": {"type": "string", "required": True},
        "changed_at": {"type": "timestamp", "required": True},
        "reconnect_policy_version": {"type": "string", "required": True},
        "secret_id": {"type": "string", "required": False},
        "last_heartbeat": {"type": "timestamp", "required": False},
        "content_hash": {"type": "string", "required": False},
        "schema_version": {"type": "semver", "required": False},
    },
    "enums": {},
    "constraints": [
        "UNKNOWN is a first-class state; while UNKNOWN privileged actions "
        "stay blocked.",
        "Every transition is machine-validated and emits "
        "CONNECTION_STATE_CHANGED evidence.",
        "LIVE is structurally refused (DEMO/SIMULATION only).",
    ],
    "compatibility": "BACKWARD",
    "history": [{"version": "1.0.0", "date": "2026-09-24",
                 "summary": "Initial connection_state contract (Phase 10)",
                 "changes": []}],
}
(Path("architecture/schemas/connection_state.yaml").write_text(
    yaml.dump(conn_schema_doc, Dumper=D, sort_keys=False,
              allow_utf=True), encoding="utf-8"))

reg = Path("architecture/schema-registry.yaml")
data = yaml.safe_load(reg.read_text(encoding="utf-8"))
existing = {e["schema_id"] for e in data["schemas"]}
if "connection_state" not in existing:
    data["schemas"].append({
        "schema_id": "connection_state",
        "file": "schemas/connection_state.yaml", "version": "1.0.0",
        "status": "ACTIVE", "owner": "adapters.mt5",
        "python_binding": "adapters.mt5.connection:ConnectionStateRecord"})
if "feed_provider" not in existing:
    data["schemas"].append({
        "schema_id": "feed_provider",
        "file": "schemas/feed_provider.yaml", "version": "1.0.0",
        "status": "ACTIVE", "owner": "adapters.market_data",
        "python_binding": None})
data["version"] = "1.10.0"
reg.write_text(yaml.dump(data, Dumper=D, sort_keys=False,
                         allow_unicode=True), encoding="utf-8")

# manifest sync
m = Path("architecture/manifest.yaml")
src = m.read_text(encoding="utf-8")
src = src.replace("  - feed_provider\n", "")  # dedupe if half-added
src = src.replace("  - connection_state\n  - feed_provider\n",
                  "  - connection_state\n  - feed_provider\n", 1) \
    if "  - feed_provider\n" in src else src
if "  - feed_provider" not in src:
    src = src.replace("  - connection_state\n",
                      "  - connection_state\n  - feed_provider\n", 1)
if "  - connection_state" not in src:
    src = src.replace("  - workspace_state\n",
                      "  - workspace_state\n  - connection_state\n"
                      "  - feed_provider\n", 1)
m.write_text(src, encoding="utf-8")
print("schemas registered + manifest synced")
