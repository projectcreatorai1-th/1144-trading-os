# PHASE 10 NOT IN SCOPE (กัน scope creep)

ห้ามทำทั้งหมดนี้ใน Phase 10 — แม้จะ "ทำได้" หรือ "ง่าย":

## LIVE Trading (ทุกรูปแบบ)
- การเปิด LIVE ใน gateway/EMS ใด ๆ
- LIVE credentials
- LIVE permissions ใหม่
- (LIVE เป็นเพียงการวิเคราะห์ใน PHASE_10_ARCHITECTURE.md/
  TEST_PLAN.md — ไม่มีโค้ด)

## Workstation redesign
- แก้ A–G layout, เพิ่ม region ใหม่, แยกหน้าเป็น feature pages
- แตะ ui/** ใด ๆ (รวม canvas chart ใหม่ — เป็น optional B1
  ต้องอนุมัติแยกและทำในส่วน presentation เท่านั้น)
- แก้ workspace_state

## Second authorities / pipelines
- feed service แยกนอก Phase 1 contracts
- direct DB writes จาก UI/adapter
- risk/portfolio/audit engine ใหม่ใด ๆ

## Direct connections
- GUI → MT5 / broker / provider
- AI → MT5 / order / RiskDecision
- Strategy → MT5

## Features อื่น
- Web workspace / mobile
- Installer/signing (ยกเว้นถ้าอนุมัติแยกเป็น packaging task)
- Real ML frameworks / ensembles / NLP adapters (GAP-020..022)
- Monte Carlo (GAP-018), multi-symbol aggregation (GAP-019)
- Asset classes ใหม่, brokers เพิ่มนอก MT5

## Production-code rule
- ใน kickoff รอบนี้: production changes = 0
- ใน implementation phase: แตะได้เฉพาะไฟล์ใน D7 ของ
  PHASE_10_DECISIONS.md; อื่น ๆ ต้องหยุดและรายงานก่อน
