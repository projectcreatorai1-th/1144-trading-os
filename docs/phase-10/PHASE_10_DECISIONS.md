# PHASE 10 DECISIONS (ตรวจสอบแล้ว ณ kickoff)

## D1 — Phase 10 คือ "Real Market Data + MT5 DEMO Connectivity"

- ทางเลือกที่พิจารณา: (a) chart/multi-monitor polish, (b) web
  workspace, (c) real data + MT5 DEMO, (d) LIVE
- เลือก (c): เป็น gap ที่ value สูงสุดของระบบเทรดจริง (ข้อมูลจริง +
  execution จริงระดับ DEMO) และเป็น prerequisite ของ LIVE ในอนาคต
- (a)(b) = optional/deferred; (d) = วิเคราะห์เท่านั้น (ดู D4)
- อ้างอิง: PHASE_9_FUTURE.md (Future Integrations/Phase 10+),
  PHASE_9_HANDOFF.md Known Limitations ข้อ 1/2/6

## D2 — Reuse-first: ไม่สร้าง pipeline/authority ใหม่

- Market data ต้องวิ่งผ่าน Phase 1 ingestion contracts เดิม
  (raw→normalized→event) — มี validation/event store/timeline
  ครบแล้ว; การสร้าง "feed service" แยกจะเป็น second pipeline
  (ผิด Section 12)
- Execution ต้องผ่าน EMS→MT5ExecutionAdapter ที่มีอยู่; การ wire
  คือ "เติม implementation ของ port" ไม่ใช่เพิ่ม engine

## D3 — DEMO first; LIVE ยัง structurally refused

- ให้ LIVE เปิดเมื่อไร = การตัดสินใจของเฟสถัดไป ๆ ต้องการ:
  DEMO evidence + reconciliation pass-rate + LIVE activation
  architecture (human-gated, governance-gated) ที่อนุมัติแยก
- Phase 10 ไม่เปลี่ยน LIVE refusal ใด ๆ

## D4 — Controlled transport สำหรับ tests; real transport
สำหรับ deployment เท่านั้น

- pattern เดิมของ Phase 5 (transport port + injected controlled
  transport) ใช้ต่อ: CI/หน่วยทดสอบไม่ต้องพึ่ง terminal จริง
- production path ห้ามใช้ controlled transport (จะเป็น fake broker
  ใน production — ผิดกฎ)

## D5 — Credentials ผ่าน SecretVault reference เท่านั้น

- adapter/connector รับ secret_id; ค่าถูกดึงตอน runtime จาก vault;
  audit บันทึก reference ไม่ใช่ค่า; สอดคล้อง SEC-009..011/058
  และ SECX-001 เดิม

## D6 — Event additions เป็น MINOR (1.3.0→1.4.0)

- 4 enum values ใหม่; additive; history บันทึกครบ; ไม่มี consumer
  เดิมใดพัง

## D7 — ไฟล์ Phase 9 ที่อนุมัติให้แตะ (เฉพาะเฟส implement)

1. registries 4 ไฟล์ (version bumps + entries)
2. architecture/validator/rules.py (+MT5X/FDX rules)
3. core/events/contracts.py (+4 enum values)
4. platform/api/desktop_gateway.py (+read-only projections)

ทุกไฟล์ที่แตะ: re-hash ใน manifest ใหม่ของ Phase 10 + บันทึกเหตุผล;
ui/** และ core engines ห้ามแก้

## D8 — Open decisions (ต้องปิดก่อน implementation)

1. แหล่ง market data: ใช้ MT5 tick stream จาก terminal เดียวกับ
   execution หรือแยก provider? (แนะนำ: เดียวกัน — dependency น้อย
   กว่า; เปิดเป็น config)
2. Scope ของ symbols ตอนเริ่ม (แนะนำ: subset เล็ก เช่น 3-5 symbols
   ที่มีอยู่ใน SYNTHETIC set เพื่อทดแทนได้ตรง ๆ)
3. นโยบาย reconnect parameters เริ่มต้น (versioned config; ต้อง
   กำหนดตัวเลขจริงตอน implement พร้อม governance)
4. การเก็บ tick history จาก feed จริง: เข้า research plane ด้วย
   หรือไม่ (แนะนำ: ใช่, ผ่าน dataset contract เดิม — ทำใน phase
   เดียวกันถ้า capacity เหลือ ไม่งั้น defer)

## Verification ของ kickoff รอบนี้ (ผลรันจริง)

- Phase 9 baseline hash: 31/31 ตรง (ไม่มีไฟล์ production เปลี่ยน)
- Architecture Validator: PASS (228 rules, 0 failures)
- Full regression: ดูผลล่าสุดด้านล่าง (รันจริงช่วง kickoff)
- Production code changes รอบ kickoff: 0 (สร้างเฉพาะเอกสาร 6 ชิ้น
  ใน docs/phase-10/)


---

# D8 CLOSED (implementation values, 2026-09-24)

- **D8.1**: MT5 tick stream จาก terminal เดียวกัน (MT5TickSource ใน
  adapters/market_data/mt5_feed.py; deployment-gated เหมือน transport)
- **D8.2**: EURUSD, XAUUSD, US30 (3 symbols — subset ของชุด SYNTHETIC
  เดิม) ใน architecture/connectivity.yaml 1.0.0; unknown = REJECTED
- **D8.3**: ค่าจริง: initial 500ms / max 8000ms / backoff ×2.0 /
  attempts 10 / connection timeout 5000ms / tick timeout 3000ms /
  stale 15000ms / unknown 30000ms / resync batch 500 / tick retention
  100,000 ticks หรือ 30 วัน (ทั้งหมดใน versioned config, fail-closed)
- **D8.4**: TickHistoryRecorder (core/research/tick_history.py) —
  ticks ผ่าน validation แล้วเข้า bounded buffer → flush เป็น
  ResearchDataset immutable (contract เดิม ไม่มี pipeline ที่สอง)
