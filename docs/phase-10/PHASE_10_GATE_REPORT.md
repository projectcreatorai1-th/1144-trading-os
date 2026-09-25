# PHASE 10 — REAL MT5 DEMO RUNTIME GATE REPORT (2026-09-24)

## Prerequisites (ตรวจจริง)

- MetaTrader5 package: **AVAILABLE** (5.0.6180 — ติดตั้งระหว่าง gate
  ตามขั้น DEPLOY; deployment prerequisite ไม่ใช่ code change)
- MT5 Terminal: **AVAILABLE** (build 6213, connected)
- DEMO Account: **AVAILABLE** (MetaQuotes-Demo, trade_mode=0=DEMO,
  balance 100,000, EURUSD ticks live; login REDACTED-…60)

## Gate ผลลัพธ์ (รันจริงกับ terminal ทั้งหมด — evidence:
## PHASE_10_RUNTIME_EVIDENCE.json)

| Gate | ผล | หลักฐานจริง |
|---|---|---|
| prerequisites | PASS | account/terminal/symbols จาก mt5 จริง |
| real_connection | PASS | CONNECTING→CONNECTED ผ่าน MT5TickSource.connect() จริง |
| real_ticks_pipeline | **FAIL** | Finding 1: server-local epoch (+3h) → FDX-TIME ปฏิเสธ (fail-closed ทำงานถูก) |
| long_run_bounded (30s) | **FAIL** | ติด Finding 1 เดียวกัน (ไม่มี tick ผ่านมาก) |
| demo_execution_full_chain | **FAIL** | Finding 2: margin_mode=2(HEDGING) แม็บผิด→UNKNOWN→EXEC-003 บล็อก (fail-closed ทำงานถูก) |
| reconciliation_real | PASS | Phase 2 engine กับ snapshot จริง: positions MATCH, account MATCH |
| safety_pause_blocks | PASS | PAUSE จริง → order ใหม่ REJECTED โดย hard policy (R-HALT-PAUSE) |
| disconnect_reconnect_real | PASS | mt5.shutdown() จริง → DISCONNECTED + freshness DISCONNECTED → initialize() คืน → CONNECTED |
| live_safety | PASS | LIVE monitor refused + account trade_mode=DEMO ยืนยัน |

## Production findings (SECTION 11 — รายงาน ไม่แก้)

สอง findings ครบตามฟอร์แมต (symptom/reproduction/component/root
cause/affected contract/proposed fix) ใน
[PHASE_10_RUNTIME_FINDINGS.md](PHASE_10_RUNTIME_FINDINGS.md):

1. **MT5 tick time เป็น server-local epoch ไม่ใช่ UTC** (skew วัดได้
   +10,800,490 ms ≈ เป๊ะ 3 ชม. = EET ของ MetaQuotes-Demo) —
   `adapters/market_data/mt5_feed.py::MT5TickSource`
2. **margin_mode enum แม็บผิด** (จริง: 0=NETTING, 1=EXCHANGE,
   2=HEDGING — account นี้เป็น 2) —
   `adapters/mt5/transport.py::MetaTrader5Transport.account_info`

ข้อสังเกตสำคัญ: **ความปลอดภัยทำงานถูกทั้งสองกรณี** — ข้อมูลเวลา
ผิดความหมายถูกปฏิเสธ (ไม่ปล่อยผ่าน) และ execution ที่ semantics
UNKNOWN ถูกบล็อก ไม่มี fake success ใด ๆ ระหว่าง gate

## Final validation (รันใหม่ทั้งหมดหลัง gate)

- Full regression: **2019 passed / 0 failed / 1 skipped** (594.59s) —
  skip เดียวคือ test เส้นทาง "package absent" ซึ่งรันไม่ได้เมื่อ
  package ถูกติดตั้งแล้ว (เหตุผลแสดงใน skip reason ชัดเจน — ไม่ใช่
  skip เพื่อให้ผ่าน)
- Architecture Validator: **PASS — 234 rules / 0 failures**
- Windows Build: **PASS (9/9)**
- Production code changes ระหว่าง gate: **0** (แก้เฉพาะ
  tools/p10_runtime_gate.py = เครื่องมือวัดของ gate เอง)

```
========================================
1144 TRADING OS
PHASE 10 — REAL MT5 DEMO RUNTIME GATE
========================================
MT5 Package: AVAILABLE (5.0.6180)
MT5 Terminal: AVAILABLE (build 6213)
DEMO Account: AVAILABLE (MetaQuotes-Demo, REDACTED)

Real MT5 Connection: PASS
Real Tick Stream: FAIL (Finding 1 — server-local epoch)
Phase 1 Pipeline: PASS (contracts; blocked by Finding 1 upstream)
DEMO Execution: FAIL (Finding 2 — enum mapping; EXEC-003 fail-closed)
OMS/EMS: PASS (boundary enforced — blocked UNKNOWN correctly)
Risk Boundary: PASS (PAUSE blocked real order)
Reconciliation: PASS (positions+account MATCH vs real broker)
Disconnect/Recovery: PASS (real mt5.shutdown/initialize)
Research Tick History: PASS (contract-verified; no real ticks yet)
Architecture Validator: 234/234 · Corruption: 9/9
Security/Quality Audit: PASS · LIVE Safety: PASS
Windows Build: PASS
Full Regression: 2019 passed / 0 failed / 1 skipped (documented)
Runtime Evidence: YES (PHASE_10_RUNTIME_EVIDENCE.json)
Production Code Changes During Gate: 0
Critical Gaps: 0 · Open Production Findings: 2 (await fix command)
========================================
PHASE 10: RUNTIME GATE BLOCKED
(2 production findings — fix requires
explicit command per SECTION 11)
========================================
STOP
========================================
```
