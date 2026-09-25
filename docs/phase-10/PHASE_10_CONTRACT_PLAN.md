# PHASE 10 CONTRACT PLAN (DESIGN ONLY — NOT IMPLEMENTED)

หลักการ: `REUSE → EXTEND → VERSION` — ส่วนใหญ่ของ Phase 10 ใช้
contracts ที่มีอยู่แล้วทั้งหมด; ของใหม่มีเพียง 2 schemas + 4 event
enum values ทั้งหมด MINOR/BACKWARD

## 1. Contracts ที่ REUSE โดยไม่แก้ (เป็นรากของแผน)

| Contract | Version | บทบาทใน Phase 10 |
|---|---|---|
| raw_data / normalized_data / event | 1.x (Phase 1) | market data เดินทางผ่าน pipeline เดิม |
| order / execution_report | 1.x (Phase 5) | execution path เดิม |
| adapters.mt5 contracts (MT5Transport/MT5ExecutionAdapter) | 1.0.0 | adapter จริง implement ตาม port นี้ |
| outbox_message | 1.x | EMS outbox เดิม |
| position / ledger_entry / reconciliation_result | 1.x | projection + reconciliation เดิม |
| risk_context (+risk-config critical-unknown) | 1.x | data UNKNOWN → BLOCK ผ่านกลไกเดิม |
| security_credential / security_secret_reference | 1.0.0 (P8) | broker/provider creds |
| workspace_state | 1.0.0 (P9) | ไม่แตะ |
| permission registry | 1.1.0 (P8) | ไม่มี permission ใหม่ |

## 2. Contracts ใหม่ (ต้องสร้างใน implementation phase)

### 2.1 `connection_state` 1.0.0 — owner: adapters.mt5

- schema name: connection_state; file schemas/connection_state.yaml;
  python binding adapters.mt5.contracts:ConnectionStateRecord
- producer: MT5Transport wrapper / feed connector
- consumers: EMS (routing decisions), DesktopGateway (read-only
  projection), audit events
- fields (draft): connection_id (identifier), environment (DEMO only
  ใน Phase 10), provider, state enum (DISCONNECTED/CONNECTING/
  CONNECTED/DEGRADED/UNKNOWN), last_heartbeat, stale_after_ms,
  reconnect_policy_version, secret_id (reference เท่านั้น), content_hash
- compatibility: BACKWARD (new schema)
- failure semantics: UNKNOWN เป็น state จริง; ทุก transition ผูก
  audit event; ห้าม UNKNOWN→CONNECTED โดยไม่มี heartbeat evidence
- audit implications: ทุก transition ปล่อย CONNECTION_STATE_CHANGED
  + เขียน audit record (Phase 8 chain)
- migration: none (new)

### 2.2 `feed_provider` 1.0.0 — owner: core.data

- schema name: feed_provider; binding core.data contracts (extend)
- producer: MarketDataAdapter registry
- consumers: ingestion pipeline, freshness stamping, gateway
- fields (draft): provider_id, adapter_version, symbols, environment,
  freshness_policy (stale_after, unknown_after), dedup_policy_version,
  content_hash
- compatibility: BACKWARD (new)
- failure semantics: provider failure → observations หยุด → freshness
  = DISCONNECTED/UNKNOWN; ไม่มี path ที่ missing data = SAFE
- audit: FEED_STALE / FEED_RESYNC events
- migration: none (new)

## 3. Extensions ของ contracts เดิม

### 3.1 event contract 1.3.0 → 1.4.0 (+4 enum values, MINOR/BACKWARD)

- CONNECTION_STATE_CHANGED
- FEED_STALE
- FEED_RESYNC
- RECONCILIATION_MISMATCH_DETECTED

consumers: desktop attention (ผ่าน gateway projection), audit,
operators; history entry บันทึก ADDED_ENUM_VALUE ทั้งสี่

### 3.2 architecture.yaml 1.9.0 → 2.0.0? → **ไม่** — ใช้ 1.10.0

SemVer ของ registry: เพิ่ม module responsibilities/allowed deps
ของ adapters.mt5 (impl files ใหม่) + rules ใหม่ใน validator
→ MINOR bump เป็น 1.10.0 (ไม่มี breaking)

### 3.3 schema-registry 1.9.0 → 1.10.0 (+2 schemas)

### 3.4 identifiers 1.8.0 → 1.9.0 (+2 kinds)

- connection_id (prefix cnn)
- feed_provider_id (prefix fdv)

## 4. สิ่งที่ต้องมีใน plan แต่ "ไม่" สร้าง schema ใหม่

- Reconciliation loop ใช้ reconciliation_result เดิม; "mismatch →
  attention" เป็น gateway projection + event ใหม่ (ไม่ใช่ contract
  ใหม่)
- Slippage/latency evidence: อยู่ใน execution_report provenance +
  latency_report (มีอยู่แล้ว) — ไม่ซ้ำ
- Broker account snapshot: เป็น external observation (Phase 2
  ExternalObservation มีอยู่แล้ว)

## 5. Production-code touch list (ขออนุมัติเป็นการเฉพาะตอน implement)

ไฟล์เหล่านี้อยู่ใน Phase 9 hash manifest — การแก้ต้อง re-hash +
บันทึกใน contract baseline:

1. architecture/validator/rules.py (+MT5X-001.., +FDX-001.. rules)
2. architecture/architecture.yaml, schema-registry.yaml,
   identifiers.yaml, manifest.yaml (version bumps + entries)
3. platform/api/desktop_gateway.py (+read-only projections:
   connection/feed/reconciliation status — ไม่แตะ authority)
4. core/events/contracts.py (+4 enum values พร้อม history)

ไฟล์อื่นที่ hash ไว้ (ui/**, core engines, platform.security/audit,
P9 tests) **ห้ามแก้**

## 6. Validator rules ที่จะเพิ่ม (design; implement ในเฟสถัดไป)

- MT5X-001: adapters.mt5 import ได้เฉพาะจาก core.ems/transport layer
  (ห้ามจาก UI/AI/strategy)
- MT5X-002: MetaTrader5 package ต้องเป็น optional import
  (deployment-gated) เหมือนเดิม
- MT5X-003: connection_state ต้องมี UNKNOWN + audit on transition
- MT5X-004: creds ต้องมาจาก secret reference เท่านั้น
- FDX-001: feed observations ต้องมี freshness metadata
- FDX-002: ห้าม second market-data pipeline (ต้องผ่าน Phase 1
  contracts)
- ทุก rule มี positive + corruption test

## 7. Migration

ไม่มี migration ที่จำเป็น: ทุกการเปลี่ยนเป็น additive (schemas ใหม่,
enum ใหม่, rules ใหม่) — consumers เดิมทั้งหมดทำงานต่อได้ทันที
(workstation ไม่รู้จัก events ใหม่ก็ไม่พัง — event contract
เป็น additive enum)
