# PHASE 10 ARCHITECTURE (DESIGN ONLY — NOT IMPLEMENTED)

## Component Architecture

```
[MT5 Terminal / Provider]  (external, DEMO)
        ↓ (transport port)
MetaTrader5Transport            [exists: adapters/mt5/transport.py]
        ↓
 ┌──────┴───────────────────────────────┐
 │ MarketDataAdapter (A1)               │   [port exists; A1 = real impl]
 │  → Raw immutable (Phase 1)           │
 │  → Normalizer → event_time stamping  │
 │  → Dedup (event id) → ordering       │
 │  → Freshness stamp (CURRENT/STALE/   │
 │    UNKNOWN/DISCONNECTED)             │
 └──────┬───────────────────────────────┘
        ↓ Phase 1 ingestion contracts (unchanged)
   Event Store / Timeline → Core consumers (risk context market.state,
   intelligence PIT datasets, desktop E/F projections)

 ┌──────┴───────────────────────────────┐
 │ MT5ExecutionAdapter (A2)             │   [exists: adapters/mt5/execution.py]
 │  canonical ↔ MT5 mapping (exists)    │
 │  idempotency keys (exists)           │
 │  retcode normalize (exists)          │
 └──────┬───────────────────────────────┘
        ↓ registered under EMS for environment=DEMO ONLY
   EMS → OMS → Risk/Policy/Strategy/Portfolio (all unchanged)
        ↓ fills/rejects
   ExecutionProjector → Position/Ledger (Phase 2, unchanged)
        ↓
   Reconciliation loop (Phase 2 engine): OMS vs EMS vs broker vs
   position vs ledger → mismatch = ATTENTION + evidence (A2)

 Connection State Plane (A3): per-connection contract; evidence only,
 never an authority; drives GUI top-bar + fail-closed behavior
```

## Data Flow (Market Data — Section 6 ของคำสั่ง)

```
Provider → Adapter → Raw Immutable Data → Normalizer →
Timestamp/Event Time → Freshness → Deduplication → Ordering →
Market State → Core Consumers
```

- ทุกขั้นใช้ contracts ที่มีแล้วของ Phase 1 (raw_data, normalized_data,
  event) — ไม่มี second pipeline
- Freshness เป็น metadata ของ observation (ไม่แก้ event contract):
  CURRENT / STALE / UNKNOWN / DISCONNECTED; delayed data = STALE;
  missing = UNKNOWN → `UNKNOWN ≠ SAFE`: data.quality UNKNOWN ทำให้
  RiskContext critical-unknown fields BLOCK (มีอยู่แล้วใน risk-config)
- Duplicate events: dedup ด้วย event id (มีแล้ว); out-of-order:
  ordering ตาม event_time + available_time (PIT rules เดิม)
- Reconnect → resync: gap detection ด้วย last event time; resync
  แบบ replay เข้า pipeline เดิม; provider failure → DISCONNECTED
  state + stale labeling (never silently treated as CURRENT)

## Authority Flow (ต้องพิสูจน์ได้)

```
GUI (Control Plane)
  ↓ DesktopGateway (platform.api)
Security (Phase 8) → Governance (Phase 8)
  ↓
Core Authority: Policy → Risk → Strategy → Intent → Portfolio →
Risk(RiskDecision) → OMS → EMS
  ↓
MT5 Adapter (DEMO) → Broker
```

ห้าม: `GUI → MT5`, `AI → MT5`, `Strategy → MT5`
(บังคับโดย validator rules ที่มีอยู่: GUI-004, AI-001..003,
EXEC rules ของ Phase 5; Phase 10 implementation เพิ่ม rules
MT5X-*** เพื่อกัน direct import จากนอก EMS/transport)

## Event Flow

ใหม่ (enum values; event contract 1.3.0 → 1.4.0 MINOR):
- CONNECTION_STATE_CHANGED (connection lifecycle evidence)
- FEED_STALE / FEED_RESYNC (data plane health)
- RECONCILIATION_MISMATCH_DETECTED (Phase 2 loop → attention)

ทุก event: schema-versioned, immutable, causally linked,
environment-aware (ตาม contract เดิม)

## Security Flow

- Credentials: MT5/provider creds เข้าผ่าน Phase 8 SecretVault
  (reference only); adapter รับ secret_id ไม่ใช่ค่า; ห้ามปรากฏใน
  source/config/log/audit (SECX/SEC rules เดิม + rule ใหม่ MT5X)
- Authorization: ไม่มี permission ใหม่; DEMO execution ใช้ DEMO_TRADE
  ตาม registry เดิม
- Governance: connection bring-up/teardown ของ DEMO execution เป็น
  governed config change (GovernanceGate, artifact type
  ENVIRONMENT_CONFIG เดิม)

## Failure Flow (failure-first ต่อ capability)

| Capability | Failure states (นิยามก่อน success) | → Recovery | → Audit | → Retry policy |
|---|---|---|---|---|
| Feed connection | DISCONNECTED, UNKNOWN, TIMEOUT | exponential backoff (bounded, deterministic policy versioned ใน config), resync on reconnect | CONNECTION_STATE_CHANGED + FEED_STALE/RESYNC | RETRYABLE จนกว่า policy cap; ไม่ retry ข้าม UNKNOWN |
| Feed data | STALE, DUPLICATE, OUT_OF_ORDER, missing(UNKNOWN) | dedup by id; order by event_time; stale → label + risk block path | FEED_STALE | ไม่ retry ข้อมูลเก่า — รอ resync |
| Order submit | REJECTED, FAILED, TIMEOUT, UNKNOWN, DUPLICATE | UNKNOWN ตาม semantics Phase 5 (never FAILED) | execution evidence → audit chain | NON_RETRYABLE: hash/schema mismatch, permission; RETRYABLE: transient; same idempotency key |
| Partial fill | PARTIAL | OMS accumulate (มีอยู่); reconciliation | execution report evidence | ไม่ auto-retry ส่วนที่เหลือ — เป็น decision ใหม่ของ strategy |
| Cancel/Modify | REJECTED, TIMEOUT, UNKNOWN | state machine order เดิม | audit เดิม | same classification |
| Reconciliation | RECONCILIATION_MISMATCH | ATTENTION + drill-down; ห้าม auto-fix จาก GUI (Phase 2 report-only เดิม) | reconciliation records เดิม | operator-driven |
| Authorization | UNAUTHORIZED, RATE_LIMITED | fail closed (Phase 8 เดิม) | audit chain | ไม่ retry |

## Recovery Flow

- Process crash ระหว่าง connection: on startup สร้าง state ใหม่จาก
  append-only stores (Phase 1/2 เดิม); connection state เริ่ม
  DISCONNECTED → CONNECTING; ไม่มี hidden state
- Unknown in-flight orders ตอน reconnect: query broker → reconcile
  → หากพบ fill ที่ไม่เคย ingest → ingest ผ่าน OMS idempotent path
  (execution_id dedup มีอยู่); หากไม่กระจ่าง → UNKNOWN evidence
  ไม่ใช่ assume-not-filled
- Feed gap: resync ผ่าน pipeline; historical backfill ต้องผ่าน
  research plane (PIT) เท่านั้น ไม่ปนกับ live timeline

## Integration Boundaries

- adapters.mt5 ↔ core: ผ่าน EMS adapter port + ExecutionReport
  contracts เท่านั้น (เดิม)
- adapters.market_data ↔ core: ผ่าน Phase 1 ingestion contracts เท่านั้น
- platform.api ↔ feed/connection: read-only projections เพิ่มใน
  DesktopGateway (ไม่แตะ authority)
- Desktop: ไม่มี import ใหม่ (ยัง platform.api + architecture.contracts)

## Phase 9 Boundary (ห้ามแตะเมื่อ implement)

- ui/** : เปลี่ยนไม่ได้ (projections ใหม่ทำใน gateway + model ที่มี
  อยู่ โดยไม่ redesign A–G)
- Core engines: reuse เท่านั้น
- workspace_state / A–G layout: ไม่เปลี่ยน
- Phase 9 hash manifest: ไฟล์ production ที่ hash ไว้ต้องไม่เปลี่ยน
  ยกเว้น gateway/rules ที่ Phase 10 scope อนุมัติเป็นการเฉพาะ
  (ต้อง re-hash + document ใน contract plan)
