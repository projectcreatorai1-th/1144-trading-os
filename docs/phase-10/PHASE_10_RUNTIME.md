# PHASE 10 RUNTIME (Real Market Data + MT5 DEMO)

## Installation requirements

- Python 3.10+ (โปรเจกต์ใช้ 3.12), PyYAML, stdlib tkinter (desktop)
- **MetaTrader5 Python package** — `pip install MetaTrader5`
  (ยังไม่ได้ติดตั้งบนเครื่องพัฒนา: runtime gate จึงเป็น BLOCKED ดู FINAL_REPORT)
- **MT5 terminal** ติดตั้งและรันอยู่ (path มาตรฐานหรือ terminal_path)
- **บัญชี MT5 DEMO** ที่ใช้งานได้ (credentials อยู่ที่ terminal ฝั่ง
  เครื่องผู้ใช้ — ระบบไม่เก็บใน source/config/log)

## MT5 setup

1. ติดตั้ง terminal + login ด้วยบัญชี DEMO
2. Enable Algo Trading ใน terminal
3. `pip install MetaTrader5` ใน environment ของโปรเจกต์

## Supported symbols (D8.2 — explicit, versioned)

`architecture/connectivity.yaml` -> symbols: **EURUSD, XAUUSD, US30**
(3 symbols, subset เดียวกับชุด SYNTHETIC เดิม) — symbol อื่นถูก
REJECTED/NOT_SUPPORTED ที่ subscribe() (ไม่มี silent subscription)

## Configuration (versioned 1.0.0, fail-closed)

| กลุ่ม | ค่าจริงที่เลือก |
|---|---|
| environment | DEMO (LIVE ห้ามปรากฏเป็นค่า — validate ต้นทาง) |
| reconnect.initial_retry_delay_ms | 500 |
| reconnect.max_retry_delay_ms | 8000 |
| reconnect.backoff_multiplier | 2.0 |
| reconnect.max_retry_attempts | 10 |
| reconnect.connection_timeout_ms | 5000 |
| freshness.tick_stream_timeout_ms | 3000 (CURRENT ภายในนี้) |
| freshness.stale_threshold_ms | 15000 (STALE) |
| freshness.unknown_threshold_ms | 30000 (เกินนี้ = UNKNOWN) |
| resync.batch_size | 500 |
| resync.reconcile_positions/account | true |
| tick_history.max_ticks | 100000 |
| tick_history.max_age_days | 30 |

ค่าอื่น ๆ (retry delay เต็มวง: 500ms→1s→2s→4s→8s(cap)) — config
ผิดรูปแบบ = STARTUP FAILURE (ContractError) ไม่มี fallback

## Startup

```python
from platform.api.connectivity_plane import ConnectivityPlane
plane = ConnectivityPlane(pipeline=..., reconciliation_engine=...)
plane.start()     # CONNECTING -> CONNECTED หรือ DEPLOYMENT_BLOCKED
plane.poll()      # ticks -> Phase 1 pipeline (จริง)
```

## Connection states (Phase 0 machine: connection_state)

DISCONNECTED → CONNECTING → CONNECTED → (DEGRADED | STALE | UNKNOWN)
STALE → RECONNECTING → RESYNCING → RECONCILING → CONNECTED
DISCONNECTED → UNKNOWN; UNKNOWN → RECONNECTING | DISCONNECTED

- UNKNOWN เป็น state จริง: ระหว่าง UNKNOWN, freshness=UNKNOWN และ
  privileged path บล็อกตามนโยบายเดิม (data/execution UNKNOWN → BLOCK)
- ทุก transition: machine-validated + CONNECTION_STATE_CHANGED event

## Reconnect behavior

สอง path ตามกฎหมายของ machine (reconnect_cycle):
- STALE/CONNECTED → RECONNECTING → RESYNCING → RECONCILING → CONNECTED
  (เมื่อ source available; fail ที่จุดวิกฤต → UNKNOWN)
- DISCONNECTED → CONNECTING → CONNECTED (สร้างใหม่แบบง่าย; retry
  ตาม backoff 500ms×2^n สูงสุด 8s, 10 ครั้ง)

## Reconciliation

MT5ReconciliationService ขับ Phase 2 engine เดิม (report-only):
- ตำแหน่ง: broker snapshot เทียบ Core projection → MATCH/MISMATCH
- บัญชี: equity เทียบค่าภายใน → MATCH/MISMATCH
- Mismatch → RECONCILIATION_MISMATCH_DETECTED event + evidence
  (ไม่มี auto-fix จาก GUI/adapter ใด ๆ)

## Safety controls

- LIVE refused เชิงโครงสร้าง: ConnectionMonitor / ReconciliationService /
  ConnectivityConfig / plane — ทุกจุด validate environment ∈ {DEMO,
  SIMULATION}; config ไม่มีค่าใดตั้งเป็น LIVE ได้ (test ยืนยัน)
- คำสั่งเทรดยังเดิน gateway→security→intent→portfolio→risk→OMS→EMS
  เช่นเดิม (Phase 10 ไม่แตะ authority ใด)

## Troubleshooting

- `DEPLOYMENT_BLOCKED` จาก plane.start() = MetaTrader5 package หรือ
  terminal ไม่พร้อม (เหตุผลระบุใน reason) — ไม่ใช่ bug; ติดตั้งแล้วรันใหม่
- Feed หยุด → freshness STALE→UNKNOWN ตาม threshold; UNKNOWN ไม่เคย
  แปลว่า SAFE
- Mismatch ซ้ำ → เปิด attention/drill-down; อย่าแก้ state ตรง

## Known limitations

- ยังไม่มี runtime จริงบนเครื่องพัฒนา (package ไม่ติดตั้ง) → runtime
  gate BLOCKED จนกว่าจะ deploy ตามข้างบน
- Tick stream เป็น pull-based poll (ไม่ใช่ push callback) — เหมาะกับ
  desktop single-process; push รอ optimization อนาคต
- Tick history buffer เป็น in-memory bounded (ตาม config) — flush
  เป็น dataset แบบ immutable
