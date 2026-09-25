# PHASE 10 RUNTIME GATE — PRODUCTION FINDINGS (SECTION 11 REPORT)

ห้ามแก้ production code ระหว่าง Runtime Gate — สอง findings นี้รอ
**explicit fix command** ก่อนถึงจะแก้ได้

## FINDING 1 — MT5 tick timestamps are SERVER-LOCAL epoch, not UTC

- **Symptom**: real ticks ถูกปฏิเสธที่ adapter ด้วย
  `[FDX-TIME] tick.ingestion_time: tick.ingestion_time before
  event_time (provider timestamp must not postdate ingestion)`
- **Component**: `adapters/market_data/mt5_feed.py` →
  `MT5TickSource.ticks()` (บรรทัด `datetime.fromtimestamp(info.time,
  tz=...)`)
- **Reproduction**: `python tools/p10_runtime_gate.py` → gate
  `real_ticks_pipeline` / `long_run_bounded` FAIL ทุกครั้ง;
  วัด skew โดยตรงได้ **+10,800,490 ms ≈ เป๊ะ 3 ชั่วโมง** (EET =
  UTC+3 ของ MetaQuotes-Demo) — เทียบกับที่ tolerance ปกติควรเป็น
  มิลลิวินาที
- **Root cause**: MetaTrader5 package คืน `tick.time` / `tick.time_msc`
  เป็น epoch ตาม **เวลาท้องถิ่นของ trade server** ไม่ใช่ UTC epoch;
  โค้ดปัจจุบันตีความเป็น UTC โดยตรง (`fromtimestamp(info.time, tz)`)
  เลยได้ event_time ล้ำหน้านาฬิกา local 3 ชั่วโมง
- **Affected contract**: `Tick` (FDX-TIME: ingestion_time >= event_time);
  ปล่อยไว้จะไม่มี tick จริงตัวไหนผ่าน pipeline ได้เลย (fail-closed
  ทำงานถูก — ปัญหาคือความหมายเวลาผิด ไม่ใช่การตรวจผิด)
- **Evidence**: docs/phase-10/PHASE_10_RUNTIME_EVIDENCE.json
  (`gates[*].detail`) + การวัด skew 5 รอบใน log ของ gate run
- **Proposed fix** (ยังไม่แก้): ใน `MT5TickSource` คำนวณ server→UTC
  offset ครั้งเดียวตอน connect (เทียบ `symbol_info_tick` กับ local
  UTC ผ่านสัญลักษณ์ที่มี spread ปกติ หรือใช้ค่า offset จาก
  `terminal_info`) แล้วลบ offset ออกจาก `time`/`time_msc` ก่อนสร้าง
  `Tick.event_time`; sequence ยังใช้ time_msc ดิบได้ (เป็นแค่ identifier)

## FINDING 2 — margin_mode enum mapping ผิดกับ MetaTrader5 จริง

- **Symptom**: สั่ง order จริงถูกบล็อกที่ EMS ด้วย
  `[EXEC-003] ems.submit: Adapter capability UNKNOWN blocks execution
  (position semantics unknown)`
- **Component**: `adapters/mt5/transport.py` →
  `MetaTrader5Transport.account_info()` (mapping `margin_mode`)
- **Reproduction**: account จริง MetaQuotes-Demo มี `margin_mode = 2`;
  โค้ดแม็บเพียง `0→NETTING, 1→HEDGING, else UNKNOWN` → ค่า 2
  (ซึ่งตาม package enum คือ `ACCOUNT_MARGIN_MODE_RETAIL_HEDGING`)
  ตกไป UNKNOWN
- **Root cause**: enum จริงของ MetaTrader5 package:
  `0=RETAIL_NETTING, 1=EXCHANGE, 2=RETAIL_HEDGING`; ตัวแปลใน
  transport ใช้ค่าคงที่ผิดชุด (สมมติ 1=HEDGING)
- **Affected contract**: `AdapterCapability.position_semantics`
  (EXEC-003 fail-closed ทำงานถูกต้อง — ไม่มี execution จนกว่า
  semantics ชัด)
- **Proposed fix** (ยังไม่แก้): แม็บตาม enum จริง
  `0→NETTING, 1→UNKNOWN(EXCHANGE, ไม่ใช่ retail semantics), 
  2→HEDGING` โดยใช้ค่าคงที่จากตัว package เอง
  (`mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` ฯลฯ) แทนเลข hard-code
- **หมายเหตุ**: การ block นี้คือความปลอดภัยทำงานถูก — เจตนาของ
  EXEC-003 คือห้าม execute เมื่อ semantics UNKNOWN

## ผลรวมของ gate (หลังแก้ bug ของ driver เองเท่านั้น)

PASS: prerequisites / real_connection / reconciliation / safety_pause
(PAUSE บล็อก order จริง) / disconnect_reconnect (คำสั่ง real mt5
shutdown) / live_safety
FAIL (ติด 2 findings ข้างบน): real_ticks_pipeline, long_run_bounded,
demo_execution_full_chain

Production code changes ระหว่าง gate: **0** (เฉพาะ driver
`tools/p10_runtime_gate.py` ซึ่งเป็นเครื่องมือวัดของ gate เอง)
