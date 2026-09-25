# GUI INTERACTION DIAGNOSIS — ROOT CAUSE REPORT (2026-09-24)

อาการ: เปิด 1144 Trading Workstation ได้ แต่กดปุ่ม / Navigation /
Controls แล้ว "ไม่มีการตอบสนอง"

## DIAGNOSIS COMPLETE — ROOT CAUSE FOUND (3 รายการ)

การวินิจฉัยทำจาก **entry point จริงที่ shortcut ใช้**
(`pythonw ui/desktop/run_windows.py`) ผ่าน harness
`tools/p9_gui_diagnosis.py` (เครื่องมือวัด — revert ได้, ไม่แตะ
production) + การทดสอบ focus/minimal-repro เสริม — **exception ระหว่าง
runtime ทั้งหมด: 0** และ event loop มีชีวิตตลอด (after() ยิง, resize /
minimize / restore ทำงาน) ⇒ อาการไม่ใช่ "GUI ค้าง" แต่เป็น
**การตอบสนองที่มองไม่เห็น + การบล็อกที่เงียบ**

---

## ROOT CAUSE 1 (หลัก) — RC-F: Navigation เปลี่ยน state แต่ view ไม่เปลี่ยน

- **จุดหยุดของ flow**: `USER INPUT → WIDGET ✓ → CALLBACK ✓
  (_navigate) → STATE ✓ (model.navigation) → VIEW UPDATE ✗`
- `_navigate()` ตั้ง `model.navigation` แล้วเรียก `_render()` — แต่
  `_render()` เปลี่ยนเพียง **statusbar บรรทัดล่างสุด** (ตัวอักษรเล็ก)
  B-region notebook (แท็บ Market/Workspace), ตาราง, E/F panes
  **เหมือนเดิมทุกปุ่มทั้ง 9 ปุ่ม**
- `WorkstationModel.apply_preset()` (7 presets) มีอยู่แต่ `_navigate`
  **ไม่เคยเรียก** และไม่มีโค้ดส่วนไหนแปลง preset → layout ที่มองเห็นได้
- **Evidence**: TEST 1 — statusbar เปลี่ยน
  `'SIMULATION != DEMO != LIVE' → 'Execution | OVERVIEW | contexts: 0'`
  แต่ `b_notebook.tabs()` เทียบก่อน/หลัง **เหมือนเดิมปุ๊ด** และ
  market-table rows เทียบก่อน/หลังกด Research **เหมือนเดิม**
- ไฟล์/ฟังก์ชัน: `ui/desktop/shell.py::_navigate` + `_render`
  (และ absence of preset→layout mapping)

## ROOT CAUSE 2 — action buttons บล็อกแบบเงียบ (callback ทำงาน, ผลลัพธ์ถูกต้อง, แตะผู้ใช้มองไม่เห็น)

- `viewmodels.submit_order()` และ `run_command()` **catch
  ContractError เองไว้ใน return value** (state=REJECTED) — ไม่ re-raise
- `shell._guarded()` เตรียม showwarning ไว้ **เฉพาะกรณี exception** ซึ่ง
  ไม่เกิด ⇒ กด Buy / Pause / Close Only / Emergency แล้ว **ไม่มี modal,
  ไม่มีข้อความ** — เพียง bell counter เพิ่มหลัง refresh รอบ 2 วินาที
- **Evidence**: harness — `exceptions=0`, `action_state=
  {'order:EURUSD:BUY:0.1': 'REJECTED', 'pause': 'REJECTED', ...}`,
  **ไม่มี Toplevel/modal ปรากฏเลย** ("modal shown" ไม่ถูกพิมพ์)
- สำคัญ (SECTION 8): การบล็อกนี้ **ถูกต้องตาม safety** (ยังไม่ login ⇒
  `_require_session` fail-closed = ACTION CORRECTLY BLOCKED) — ปัญหาคือ
  UI ไม่บอกอะไรผู้ใช้เลย ไม่ใช่ UI broken
- ไฟล์/ฟังก์ชัน: `ui/desktop/viewmodels.py::submit_order/run_command`
  + `ui/desktop/shell.py::_guarded`

## ROOT CAUSE 3 — entry point ที่ shortcut ใช้ ไม่มีหน้า Login (ผู้ใช้ login ไม่ได้เลย)

- `run_windows.py::launch()` = gateway → model → Shell → mainloop —
  **ไม่สร้าง LoginDialog**; `app.py::main()` มี LoginDialog แต่
  **shortcut บน Desktop ชี้ run_windows.py**
- ผล: เปิดโปรแกรม → ไม่มีทาง authenticate → `session=None` ตลอดไป →
  ทุก action ถูกบล็อก (ถูกต้อง) + เงียบ (RC-2) + navigation มองไม่เห็น
  (RC-1) = "ทุกปุ่มตาย"
- **Evidence**: TEST 4 — `LoginDialog in run_windows.launch(): False`;
  `LoginDialog in app.main(): True`; หลัง login ด้วยมือใน harness:
  `submit_order → APPLIED`, blotter แสดง 1 แถว — ทุกอย่างทำงานได้ทันที
  เมื่อมี session
- ไฟล์/ฟังก์ชัน: `ui/desktop/run_windows.py::launch`

---

## ผลตรวจส่วนที่เหลือ (reproduce จริงทั้งหมด)

- **Startup log**: exception = 0; timing บน UI thread ก่อนหน้าต่างเปิด:
  imports 153ms + gateway 515ms (ไม่มี MT5/network/blocking ใด —
  Phase 10 plane ไม่ถูก compose ในเส้นทาง GUI;
  `connectivity_status()` เป็น getattr-guard)
- **Event loop**: alive — after() ยิง, resize 900x600 ได้,
  minimize/restore ได้, close/destroy ได้
- **Search**: ทำงานเมื่อ focus อยู่ที่ช่องกรอก (`<Return>` → context
  SYMBOL EURUSD ถูกตั้ง)
- **Inspector/Blotter/E/F**: render ถูกต้องเมื่อเลือก symbol / มี order
- **Ctrl+K**: **INCONCLUSIVE โดย automation** — พิสูจน์แล้วว่า synthetic
  Control+key events ไม่ถูก deliver บนเครื่องนี้แม้ใน minimal Tk app
  (`<Escape>` ยิง ✓, Control-combo ทุกรูปแบบรวม bind_all/state-mask/
  when=tail ไม่ยิง ✗) ขณะที่ binding ใช้ canonical pattern
  (`root.bind("<Control-k>")`) — ต้องยืนยันด้วยการกดแป้นจริง 1 ครั้ง

## Interaction Matrix (ผลจริง)

| Area | Event | Callback | State | Refresh | Result | Evidence |
|---|---|---|---|---|---|---|
| Overview..Operations (9) | ✓ | ✓ | ✓ | **✗ มองไม่เห็น** | USER-PERCEIVED FAIL | tabs/rows เทียบก่อน-หลังเหมือนเดิม |
| G: Buy/Pause/CloseOnly/Emergency | ✓ | ✓ | REJECTED (ถูกต้อง) | ✗ เงียบ | BLOCKED CORRECTLY, INVISIBLE | action_state + ไม่มี modal |
| Search (Enter, focus) | ✓ | ✓ | ✓ context | ✓ | PASS | context=SYMBOL EURUSD |
| Inspector | ✓ (หลัง select) | ✓ | ✓ | ✓ | PASS | "SYMBOL: EURUSD" |
| Blotter | ✓ | ✓ | ✓ (หลัง login+order) | ✓ | PASS | 1 แถวจริง |
| Resize / Minimize / Restore / Close | ✓ | — | — | ✓ | PASS | harness ยืนยัน |
| Ctrl+K | ไม่พิสูจน์ได้ | binding ปกติ | — | — | INCONCLUSIVE (ต้องกดจริง) | minimal repro ครบทุกรูปแบบ |

## Phase 10 Relationship: **NOT RELATED**

- Phase 9 hash manifest ยืนยัน: `shell.py`, `viewmodels.py`, `app.py`,
  `run_windows.py` **UNCHANGED** ตั้งแต่ baseline — อาการนี้มีอยู่ใน
  Phase 9 ตั้งแต่ต้น (shell smoke ของเฟสนั้นตรวจเพียง
  construct/render จึงไม่เจอ)
- Phase 10 แตะเฉพาะ `desktop_gateway.py` + `core/events/contracts.py`
  (ตาม touch list ที่อนุมัติ) และเส้นทาง GUI ไม่มี MT5/blocking ใหม่

## Phase 9 Baseline

**ไม่เปลี่ยน** — hash ของไฟล์ GUI ทั้งสี่ตรงกับ manifest; runtime gate
ของ Phase 10 ยังคง `IMPLEMENTATION COMPLETE — RUNTIME GATE BLOCKED`
(2 findings ก่อนหน้า) ไม่มีการเปลี่ยนสถานะจากการวินิจฉัยนี้

## Severity

**High** (แอป "ดูเหมือนตาย" ในมุมผู้ใช้ แต่ไม่มีปัญหาความปลอดภัย/ข้อมูล —
safety ทำงานถูกทุกจุด)

## Minimal Fix (เสนอเท่านั้น — ยังไม่ implement)

1. `run_windows.py::launch()`: สร้าง `LoginDialog(shell, model)` ก่อน
   mainloop (เหมือน app.main) — แก้ RC-3 ให้ผู้ใช้ login ได้
2. `shell._navigate()`: แม็ป nav→preset แล้วเรียก
   `model.apply_preset(...)` + เปลี่ยน B-notebook tab/panel ให้เห็น
   ความต่างต่อ nav ทุกปุ่ม — แก้ RC-1
3. `shell` แสดงผล REJECTED จาก receipt (banner/modal เดียว) — แก้ RC-2
   โดยไม่แตะ viewmodel/safety
4. ยืนยัน Ctrl+K ด้วยแป้นจริง 1 ครั้ง (automation เพิ่มไม่ได้)

## Required Regression หลังแก้

Phase 9 core/shell tests + shell smoke (test_phase9_invariants_e2e)
· **เพิ่ม E2E ใหม่**: (a) nav ทุกปุ่ม → preset เปลี่ยน → มี element
มองเห็นได้เปลี่ยน (b) action ถูกบล็อก → ต้องมี feedback มองเห็นได้
(c) launch ผ่าน run_windows → LoginDialog ปรากฏ · full pytest ·
Architecture Validator · build verification

## FINAL STATUS

`DIAGNOSIS COMPLETE — ROOT CAUSE FOUND`
