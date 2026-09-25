# PHASE 10 SCOPE (KICKOFF — ARCHITECTURE ONLY, NOT IMPLEMENTED)

Status: DEFINED (this document is a plan; zero production code was
changed to produce it)

## Objective

เชื่อม 1144 Trading OS กับโลกจริงระดับ DEMO อย่างปลอดภัย:
(1) Market Data จริงจาก provider ผ่าน Data Plane เดิมของ Phase 1
(2) Execution ผ่าน MT5 DEMO ด้วย adapter contracts ที่มีอยู่แล้วของ Phase 5
โดยที่ authority ทุกตัว (Risk/OMS/EMS/Security/Governance/Audit)
ไม่เปลี่ยนและไม่ถูก bypass — LIVE ยังคงถูกปิดและเป็นเพียงการวิเคราะห์
เชิงสถาปัตยกรรมในเอกสารนี้เท่านั้น

## Current State (ตรวจจาก filesystem จริง ณ kickoff)

- Phase 9 baseline LOCKED: hash manifest 31/31 files ตรง, validator
  228 rules PASS, regression ดูผลรอบนี้ใน PHASE_10_DECISIONS.md
- MT5 adapter contracts มีแล้ว: adapters/mt5/{contracts,execution,
  transport}.py — transport เป็น port (MetaTrader5Transport
  deployment-gated; test/simulated-demo ใช้ controlled transport)
- MarketDataAdapter port มีแล้ว (adapters/market_data/contracts.py)
- EMS รับ adapter แบบ per-environment; ปัจจุบันมีเฉพาะ simulation
- DesktopGateway ทำงานกับ SIMULATION/DEMO เท่านั้น; LIVE refused
- ข้อมูลตลาดในเดสก์ท็อปเป็น SYNTHETIC ทั้งหมด

## Proposed Scope

### A. Required for Phase 10 (แกะจาก PHASE_9_FUTURE + limitations)

A1. **Market Data Plane (DEMO-grade real feed)**
    Provider (MT5 DEMO market data ผ่าน transport port หรือ provider
    อื่น) → MarketDataAdapter → Phase 1 ingestion (raw immutable →
    normalized → events) → timeline/store → Core consumers
    ครอบคลุม freshness/dedup/ordering/reconnect ตาม มาตราต่อไป

A2. **MT5 DEMO Execution Connectivity**
    Wire MetaTrader5Transport (deployment-gated) เข้ากับ
    MT5ExecutionAdapter ที่มีอยู่ → EMS adapter registry
    สำหรับ environment=DEMO พร้อม connection state machine,
    order idempotency, execution-UNKNOWN semantics, reconciliation
    ตำแหน่ง/บัญชี (Phase 2 engines), slippage/latency evidence

A3. **Connection & Session Plane**
    Connection state contract (DISCONNECTED/CONNECTING/CONNECTED/
    DEGRADED/UNKNOWN), reconnect/backoff policy แบบ deterministic,
    credential handling ผ่าน Phase 8 SecretVault (ไม่มี secret ใน
    source/config/log — ใช้ reference)

A4. **Desktop F-pane/E-pane upgrade เฉพาะ projection**
    เพิ่ม read-only projections ของ connection state, feed freshness,
    reconciliation status ใน gateway (ไม่แตะ authority, ไม่ redesign
    A–G; ใช้ region เดิม)

### B. Optional Enhancement (ทำได้แต่ไม่จำเป็น — ต้องขออนุมัติแยก)

B1. Canvas chart (แทน price table) — presentation เท่านั้น
B2. Multi-window floating panels

### C. Deferred (Phase 11+)

C1. LIVE activation (ดู PHASE_10_NOT_IN_SCOPE.md — ต้องการ architecture
    ครบชุดและ human-gated activation path)
C2. Web workspace, mobile, installer/signing
C3. Real ML frameworks (GAP-020..022), Monte Carlo (GAP-018)

### D. Out of Scope

ทั้งหมดใน PHASE_10_NOT_IN_SCOPE.md

## Limitation Analysis (Section 4 ของคำสั่ง — ทั้ง 7 ข้อ)

| # | Limitation | แก้ใน P10? | เหตุผล | Dependencies | Security | Risk | Contract | Testing | ไม่ทำ→ไปเฟสไหน |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Market data SYNTHETIC | **ใช่ (A1)** | เดสก์ท็อปต้องเห็นราคาจริงเพื่อ decision quality; pipeline มีแล้ว เหลือ adapter จริง | MT5 terminal/provider creds | creds ผ่าน vault; provider fail → UNKNOWN ไม่ใช่ SAFE | ราคา stale ต้อง block NEW_EXPOSURE ผ่าน data.quality field ที่มีอยู่ | ใช้ contracts เดิม (raw/normalized/event); +feed_provider metadata | feed failure matrix, PIT regression | P11 |
| 2 | Execution = simulation | **ใช่ (A2) DEMO เท่านั้น** | DEMO คือขั้นถัดไปที่ปลอดภัยและพิสูจน์ adapter จริง | MT5 DEMO account + terminal | DEMO creds ใน vault; adapter ผ่าน EMS เท่านั้น | execution UNKNOWN semantics มีแล้ว; เพิ่ม partial fill/reject evidence | MT5 contracts มีแล้ว 1.x; +connection_state ใหม่ | broker failure matrix ทั้งชุด (Section 11) | P11 |
| 3 | LIVE refused | **ไม่** (วิเคราะห์เท่านั้น) | LIVE ต้องการ DEMO evidence + reconciliation ผ่านมาก่อน + activation path ที่ human-gated | A2 สำเร็จ + รอบพิสูจน์ | LIVE permission มีอยู่ (ADMIN only) แต่ต้อง governance gate | สูงสุด — ห้ามเร่ง | ไม่เปลี่ยน contracts LIVE ใด ๆ | LIVE safety tests ออกแบบไว้ใน test plan | P11+ |
| 4 | Chart = table | ไม่ (B1 optional) | presentation; ไม่กระทบ safety/authority | ไม่มี | ไม่มี | ไม่มี | workspace_state ไม่เปลี่ยน | UI smoke เดิม | P11 |
| 5 | Multi-monitor single window | ไม่ (B2) | ประโยชน์ต่ำเมื่อเทียบงานเชื่อมต่อจริง | ไม่มี | ไม่มี | ไม่มี | ไม่เปลี่ยน | ไม่มี | P11+ |
| 6 | MT5 not wired | **ใช่ (A2)** | เป็นแกนของเฟสนี้ | MetaTrader5 package + terminal ที่ deployment | transport deployment-gated อยู่แล้ว; GUI ยังห้ามแตะ (GUI-004) | execution authority ยังอยู่ที่ EMS เท่านั้น | ใช้ MT5 adapter contracts เดิม | corruption + E2E + unknown-execution | P11 ถ้าไม่พร้อม dependency |
| 7 | Desktop = control plane | **คงเดิม** | เป็นข้อดี ไม่ใช่ข้อจำกัด | — | — | — | — | — | — |

## Dependencies (external, ต้องมีก่อน implementation)

1. MetaTrader5 Python package (deployment; โค้ดเดิม import แบบ
   optional อยู่แล้ว) + MT5 terminal ติดตั้ง
2. MT5 DEMO account credentials (เข้าผ่าน SecretVault reference
   เท่านั้น — ห้ามใน source/config/log)
3. Provider สำหรับ market data (ใช้ MT5 tick stream ได้)

## Risks

- Provider/broker ไม่ available ใน dev environment → architecture
  รองรับด้วย controlled transport สำหรับ tests (มี pattern นี้มา
  แล้วใน Phase 5) แต่ production path ต้องใช้ของจริงเท่านั้น
- Execution UNKNOWN จาก broker → semantics มีแล้ว (Phase 5);
  Phase 10 เพิ่ม evidence + reconciliation loop
- Feed delay/out-of-order → freshness contract + dedup by event id
  + PIT rules ที่มีอยู่

## Security

- ทุก connection ต้องผ่าน credential references (Phase 8 vault)
- GUI ยังคุยกับ gateway เท่านั้น; GUI-004 ยังห้าม adapters.mt5
- ไม่มี LIVE path ใหม่; permission model ไม่เปลี่ยน
- Audit: connection lifecycle events (new event types) + execution
  evidence เข้า audit chain เดิม

## Authority Map (Section 8)

| Capability | Authority |
|---|---|
| Authentication | Phase 8 Security |
| Authorization | Phase 8 Security |
| Governance | Phase 8 Governance |
| Audit | Phase 8 Audit |
| Risk | Risk Engine |
| Strategy | Strategy Engine |
| Portfolio | Portfolio Engine |
| OMS | OMS |
| EMS | EMS |
| Market State | Data/Core (Phase 1 pipeline) |
| AI | Advisory only |
| Desktop | Control Plane |
| **Market feed ingestion (ใหม่)** | **Data Plane (Phase 1 contracts)** |
| **Broker connection (ใหม่)** | **adapters.mt5 ใต้ EMS (Phase 5)** |
| **Connection state (ใหม่)** | **adapters.mt5 transport (เป็น evidence ไม่ใช่ authority)** |
| **Reconciliation (ใหม่ loop)** | **Phase 2 Reconciliation Engine** |

ไม่มี capability ใดมี authority ซ้ำสองแห่ง

## Contracts (สรุป — เต็มใน PHASE_10_CONTRACT_PLAN.md)

ใหม่: `connection_state` 1.0.0 (adapters.mt5), `feed_provider` 1.0.0
(core.data) — ทั้งคู่ EXTEND ไม่ทับของเดิม; events +4 enum values
(CONNECTION_STATE_CHANGED, FEED_STALE, FEED_RESYNC, RECONCILIATION_
MISMATCH_DETECTED) → event contract 1.3.0 → 1.4.0 (MINOR)

## Failure Model (สรุป — เต็มใน ARCHITECTURE doc)

ทุก capability นิยาม failure states ก่อน success: UNKNOWN, STALE,
DISCONNECTED, TIMEOUT, REJECTED, FAILED, PARTIAL, DUPLICATE,
OUT_OF_ORDER, UNAUTHORIZED, RATE_LIMITED, RECONCILIATION_MISMATCH
พร้อม `failure → recovery → audit → retry policy` ราย capability

## Testing Strategy (สรุป — เต็มใน PHASE_10_TEST_PLAN.md)

18 หมวด unit→performance + LIVE-specific 8 หมวด (ออกแบบพร้อมรอย
แต่ยังไม่ implement จนกว่า LIVE phase)

## Release Gates (สำหรับ implementation phase ถัดไป)

Full regression PASS · validator PASS (+rules ใหม่ MT5X/FDX) ·
corruption ครบทุก rule ใหม่ · E2E feed+execution บน controlled
transport · DEMO smoke ด้วย real terminal (deployment) · audits 0
violations · Phase 9 baseline hash ยังตรงสำหรับไฟล์ที่ไม่เกี่ยว
· production code changes เฉพาะที่ scope อนุมัติเท่านั้น
