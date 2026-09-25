# PHASE 10 TEST PLAN (DESIGN ONLY — NOT IMPLEMENTED)

Matrix สำหรับ implementation phase ถัดไป; ทุกหมวดต้องผ่านก่อน
release gate ของ Phase 10

## Unit
- MarketDataAdapter: normalize, event-time stamping, dedup, ordering,
  freshness stamping (CURRENT/STALE/UNKNOWN/DISCONNECTED)
- connection_state: transitions, UNKNOWN semantics, audit emission
- MT5 mapping: canonical↔MT5 (มี tests เดิม — เพิ่ม real-adapter
  cases บน controlled transport)
- Reconciler: OMS vs EMS vs broker vs position vs ledger mismatch
  detection

## Contract
- schema registry drift (schemas ใหม่ 2 ตัว + bindings)
- event contract 1.4.0 enum completeness + history
- identifiers 1.9.0 kinds
- ทุก contract ใหม่: validation fail-closed (malformed = reject)

## Failure Matrix (ต่อ capability — นิยาม failure ก่อน success)
- feed DISCONNECTED/TIMEOUT → backoff → audit → bounded retry
- feed STALE/OUT_OF_ORDER/DUPLICATE → label/dedup/order → audit
- feed UNKNOWN → BLOCK new exposure (ผ่าน data.quality critical path)
- submit REJECTED/FAILED/TIMEOUT/UNKNOWN/DUPLICATE → Phase 5
  semantics + evidence
- partial fill PARTIAL → accumulate + reconciliation
- cancel/modify REJECTED/TIMEOUT/UNKNOWN
- reconciliation mismatch → ATTENTION (ห้าม auto-fix)
- UNAUTHORIZED/RATE_LIMITED → fail closed

## Invariants (อย่างน้อย)
- data UNKNOWN ≠ SAFE (feed down → risk BLOCK path ทำงาน)
- broker disconnect ≠ safe (DEMO execution fail-closed)
- ไม่มี second pipeline/authority ใด ๆ
- GUI/AI ไม่มี path ตรงสู่ MT5
- ทุก connection transition มี audit
- ทุก order ผ่าน OMS→EMS เท่านั้น (idempotency รักษาไว้)
- SIMULATION ≠ DEMO ≠ LIVE (LIVE ยัง refused)

## Architecture Validator
- rules ใหม่ MT5X-001..004, FDX-001..002 (+corruption ทุก rule)
- รวม 228 + n rules; 0 failures

## Security
- creds เป็น reference เท่านั้น; ไม่ปรากฏใน source/config/log/audit
- secret rotation ระหว่าง connection live
- ไม่มี permission ใหม่/hidden

## Authorization
- DEMO execution ใช้ DEMO_TRADE (registry เดิม)
- trader/risk_manager personas ทำงานเหมือนเดิมบนเส้นทางใหม่

## Audit
- ทุก connection lifecycle + execution + governance change มี
  chained evidence; tamper detection ยังทำงาน

## Reconciliation
- injected mismatch (position/account) → detected + evidence +
  attention; no auto-fix

## E2E
- feed live → normalize → event → risk context → desktop projection
- order ผ่าน chain เต็มบน controlled transport (DEMO-shape)
- reconnect: disconnect ระหว่าง in-flight order → UNKNOWN handling →
  reconcile → state consistent

## Golden Path (Phase 10 version)
Login → DEMO → watch real feed → symbol context → risk check →
order (controlled transport) → fill → position/ledger → audit →
disconnect → reconnect → resync → consistent

## Disconnect
- provider/broker disconnect ทุกจุด (ก่อน submit/ระหว่าง in-flight/
  หลัง fill ก่อน report) → สถานะถูกต้องเสมอ

## Recovery
- crash ระหว่าง connection/order → restart → rebuild จาก stores →
  in-flight UNKNOWN → reconcile

## Corruption
- corrupted connection_state / feed metadata / execution evidence /
  hashes → reject (fail closed)

## Performance
- feed throughput สังเคราะห์ (SYNTHETIC benchmark; ไม่อ้าง production
  scale), ingestion latency, reconciliation cost

## Long Run
- feed ต่อเนื่อง + reconnect cycles + หน้าต่าง desktop เปิดนาน:
  หน่วยความจำนิ่ง, ไม่มี duplicate rows, ไม่มี orphan timers

## Windows Runtime
- launcher + verify-build ขยายด้วย feed/connection checks (9→12+)

## LIVE-related (ออกแบบไว้; ไม่เกิดขึ้นจนกว่าเฟส LIVE)
### LIVE Safety Tests
- LIVE activation ต้อง human-gated + governance + policy + risk;
  ห้าม bypass ด้วย GUI behavior
### Broker Failure
- ชุด retcode/timeout จริง → classification ถูกต้อง
### Duplicate Order
- idempotency key เดิม + payload ต่าง = corruption; เดียวกัน =
  idempotent
### Unknown Execution
- timeout/unknown → UNKNOWN (never FAILED) → reconcile loop
### Partial Fill
- สะสมถูก; ส่วนที่เหลือเป็น decision ใหม่
### Reconnect
- resync orders/positions/account; ไม่ duplicate ingestion
### Position Mismatch
- broker vs internal → mismatch evidence + block escalation path
### Account Mismatch
- ยอด/สถานะบัญชีต่าง → attention + evidence
### Emergency Stop
- ระหว่าง connection live: stop ทำงานผ่าน risk authority; release
  ต้อง governance
