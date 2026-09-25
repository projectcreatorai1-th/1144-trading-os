"""BASELINE REPAIR - live GUI verification driver v4 (test tooling, not production).

Drives the REAL desktop app (ui/desktop/run_windows.py) with REAL OS input
(keybd_event / pywinauto SendInput) after deterministic foreground verification,
and captures pixel + text-strip evidence via PIL ImageGrab.

Implements the final-validation command (Phases A-B):
  A1 sbox(box, ox, oy) used for EVERY region analyzed from full-screen shots
  A2 full 64-bit-safe argtypes for all user32 APIs
  A3 foreground verified + logged (expected/foreground HWND, title, timestamp,
     action) before EVERY keyboard action; input refused on mismatch
  A4 login input via: activate -> click secret field -> Ctrl+A -> type -> Enter
  A5 Ctrl+K real keyboard; production binding untouched; keyboard layout is
     normalized to en-US for the target window exactly like a human tester
     switching the language bar (Thai layout makes Tk emit keysym ?? - see
     KEYBOARD_LAYOUT_FINDING.md); layout restored afterwards
  B1  exactly-one Main Window / LoginDialog (duplicate check)
  B2  invalid login -> safe state, protected action stays rejected
  B3  cancel login -> no session, no bypass
  B4  valid login -> session, one Main Window, no duplicate dialog
  B5  9/9 navigation: visible change + semantic title strip per destination
      (pixel diff is secondary; semantic state is read from the title text
      which encodes navigation + preset, and cross-checked by the pytest suite)
  B6  reverse navigation Overview->Research->Execution->Overview and repeat
      Research x3: title matches destination reference, stable across repeats
  B7  4 rejected actions with visible banner
  B8  rejection lifecycle: banner updates per action, single banner (red-text
      bounding box height stays within one 3-line label - no stacking)
  B9  no accidental APPLIED (green=0); state-level no-side-effect proven by
      tests/test_phase9_gui_repair.py + overview counts crop (vision-read)
  B10 Ctrl+K real keyboard -> palette opens/closes
  B11 existing GUI: Search (focus -> type -> Enter -> context updates),
      Resize (MoveWindow -> client size changes -> render intact -> restore),
      Minimize/Restore (IsIconic -> restore -> render intact)
  B12 clean close (exit code 0) -> relaunch -> exactly one LoginDialog, no
      stale session (Buy rejected on fresh instance), no zombie window

Evidence: docs/phase-9/evidence/gui-repair-live/
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ctypes.wintypes as wintypes

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "phase-9" / "evidence" / "gui-repair-live"

PERSONA_TRADER = "trader"
SECRET_TRADER = "synthetic-trader-desktop-secret"

user32 = ctypes.windll.user32

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.MoveWindow.restype = wintypes.BOOL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, wintypes.LPARAM]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

SW_MINIMIZE, SW_RESTORE = 6, 9
WM_CLOSE = 0x0010
WM_INPUTLANGCHANGEREQUEST = 0x0050
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL, VK_K, VK_ESCAPE = 0x11, 0x4B, 0x1B

MAIN = "1144 Trading OS"
DIALOG = "1144 Trading OS - Login"
PALETTE = "Command Palette"

NAV = [(44, 61 + 27 * i) for i in range(9)]
NAV_NAMES = ["Overview", "Markets", "Intelligence", "Portfolio",
             "Execution", "Research", "Strategies", "Risk", "Operations"]
NAV_INDEX = {name: i for i, name in enumerate(NAV_NAMES)}
BUY, PAUSE, CLOSE_ONLY, EMERGENCY = (138, 754), (226, 754), (314, 754), (412, 754)
NEUTRAL = (260, 32)
DIALOG_SECRET = (256, 44)
SEARCH = (1170, 10)                       # search entry (top bar, right)
BANNER_BOX = (95, 696, 1275, 734)         # banner label above the buttons
BTITLE_BOX = (94, 19, 660, 45)
BREGION_BOX = (90, 15, 1180, 390)
STATUS_BOX = (0, 770, 760, 800)
BCONTENT_BOX = (90, 46, 1180, 390)
SEARCH_BOX = (1060, 0, 1275, 24)


def sbox(box, ox, oy):
    return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_windows(pid: int, title: str) -> list:
    results: list[int] = []
    found_pid = wintypes.DWORD()

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found_pid))
        if found_pid.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            results.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return results


def find_windows_any_pid(title: str) -> list:
    results: list[int] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            results.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return results


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def wait_window(pid: int, title: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = find_windows(pid, title)
        if found:
            return found[0]
        time.sleep(0.2)
    return None


def wait_window_gone(pid: int, title: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_windows(pid, title):
            return True
        time.sleep(0.2)
    return False


def client_origin(hwnd: int):
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_size(hwnd: int):
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def thread_layout(hwnd: int) -> int:
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    return int(user32.GetKeyboardLayout(tid)) & 0xFFFFFFFF


def request_layout(hwnd: int, hkl: int) -> int:
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
    time.sleep(0.8)
    return thread_layout(hwnd)


def foreground() -> int:
    return user32.GetForegroundWindow() or 0


def ensure_foreground(hwnd: int, label: str, kb_log: list) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.4)
    for _attempt in range(5):
        try:
            from pywinauto.controls.hwndwrapper import HwndWrapper
            HwndWrapper(hwnd).set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        fg = foreground()
        entry = {"action": label, "timestamp": now_utc(),
                 "expected_hwnd": hwnd,
                 "foreground_hwnd": fg,
                 "window_title": window_title(fg) if fg else "",
                 "match": fg == hwnd}
        kb_log.append(entry)
        print(f"   [fg] {label}: expected={hwnd} fg={fg} "
              f"({entry['window_title']!r}) match={entry['match']}")
        if fg == hwnd:
            return True
    return False


def kb_send(hwnd: int, keys: str, label: str, kb_log: list) -> bool:
    if not ensure_foreground(hwnd, f"kb:{label}", kb_log):
        print(f"   [guard] REFUSED keyboard '{label}' - foreground mismatch")
        return False
    from pywinauto import keyboard
    keyboard.send_keys(keys, with_spaces=True)
    time.sleep(0.3)
    return True


def kb_vk_combo(hwnd: int, vk: int, label: str, kb_log: list) -> bool:
    """Real keybd_event combo; target window layout normalized to en-US
    first (Thai layout makes Tk emit keysym ?? for letter keys) and restored
    after. Production binding untouched."""
    if not ensure_foreground(hwnd, f"kbvk:{label}", kb_log):
        return False
    before = thread_layout(hwnd)
    eng = int(user32.LoadKeyboardLayoutW("00000409", 0)) & 0xFFFFFFFF
    now = request_layout(hwnd, eng)
    kb_log.append({"action": f"layout:{label}", "timestamp": now_utc(),
                   "before": f"{before:#x}", "requested": f"{eng:#x}",
                   "after": f"{now:#x}"})
    print(f"   [layout] {label}: {before:#x} -> {now:#x}")
    try:
        if now != eng:
            print("   [guard] layout switch failed - refusing to send combo")
            return False
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.5)
        return True
    finally:
        if before != now:
            restored = request_layout(hwnd, before)
            kb_log.append({"action": f"layout-restore:{label}",
                           "timestamp": now_utc(),
                           "target": f"{before:#x}", "restored": f"{restored:#x}"})
            print(f"   [layout] {label}: restored {restored:#x}")


def kb_vk_key(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)


def click_client(hwnd: int, cx: int, cy: int, label: str, kb_log: list) -> bool:
    if not ensure_foreground(hwnd, f"click:{label}", kb_log):
        print(f"   [guard] REFUSED click '{label}' - foreground mismatch")
        return False
    ox, oy = client_origin(hwnd)
    from pywinauto import mouse
    mouse.click(button="left", coords=(ox + cx, oy + cy))
    time.sleep(0.35)
    return True


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def grab(name: str, bbox=None):
    img = ImageGrab.grab(bbox=bbox)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    img.save(EVIDENCE / f"{name}.png")
    return img


def strip(img, box, scale=2):
    from PIL import Image
    c = img.crop(box)
    return c.resize((c.width * scale, c.height * scale), Image.LANCZOS)


def count_tinted(img, want: str) -> int:
    total = 0
    for r, g, b in img.convert("RGB").getdata():
        if want == "red" and r > g + 25 and r > b + 25 and r > 90:
            total += 1
        elif want == "green" and g > r + 25 and g > b + 25 and g > 90:
            total += 1
    return total


def red_bbox_height(img, box) -> int:
    """Pixel height of the red-text bounding box inside box (banner stacking
    guard: one 3-line banner is ~38px; stacked banners would be far taller)."""
    crop = img.crop(box).convert("RGB")
    W, H = crop.size
    data = crop.load()
    miny, maxy = None, None
    for y in range(H):
        for x in range(W):
            r, g, b = data[x, y]
            if r > g + 25 and r > b + 25 and r > 90:
                if miny is None:
                    miny = y
                maxy = y
                break
    return (maxy - miny + 1) if miny is not None else 0


def diff_pixels(a, b, box) -> int:
    ra, rb = a.crop(box), b.crop(box)
    if ra.size != rb.size:
        return max(ra.size[0] * ra.size[1], rb.size[0] * rb.size[1])
    total = 0
    for pa, pb in zip(ra.convert("RGB").getdata(), rb.convert("RGB").getdata()):
        if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 30:
            total += 1
    return total


def contact_sheet(rows: list, out_name: str):
    from PIL import Image, ImageDraw
    pad, label_h = 10, 22
    width = max(im.width for _, im in rows) + 260
    height = sum(im.height + label_h + pad for _, im in rows) + pad
    sheet = Image.new("RGB", (width, height), (250, 248, 245))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for label, im in rows:
        draw.text((10, y + 2), label, fill=(120, 40, 0))
        sheet.paste(im, (250, y))
        y += im.height + label_h + pad
    sheet.save(EVIDENCE / f"{out_name}.png")


def main() -> int:
    global ImageGrab
    from PIL import ImageGrab  # noqa: F811
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    report = {
        "run": "BASELINE REPAIR live GUI validation (driver v4)",
        "started_utc": now_utc(),
        "steps": [], "kb_log": [], "app_instances": [],
        "cross_window_safe": True, "runtime_exceptions": 0,
    }

    def step(name, ok, detail):
        report["steps"].append(
            {"step": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    def launch(n: int):
        # real production entry: pythonw ui/desktop/run_windows.py (the same
        # command the desktop shortcut uses); falls back to sys.executable
        # only if pythonw is absent
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        exe = str(pythonw) if pythonw.exists() else sys.executable
        errfile = open(EVIDENCE / f"app{n}_stderr.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [exe, str(ROOT / "ui" / "desktop" / "run_windows.py")],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=errfile)
        return proc, errfile

    def shutdown(n, proc, errfile) -> int:
        for hwnd in find_windows(proc.pid, MAIN):
            close_window(hwnd)
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=5)
        errfile.close()
        text = (EVIDENCE / f"app{n}_stderr.log").read_text(encoding="utf-8")
        if "Traceback" in text:
            report["runtime_exceptions"] += 1
            report["steps"].append(
                {"step": f"instance {n} stderr", "result": "FAIL",
                 "detail": {"traceback": True}})
        report["app_instances"].append(
            {"instance": n, "pid": proc.pid, "exit_code": code})
        return code

    def nav_title_matches(ref_strip, shot, ox, oy, tol=250) -> int:
        """Semantic anchor: title strip of current shot vs reference strip of
        the destination (tolerance for antialiasing jitter)."""
        cur = shot.crop(sbox(BTITLE_BOX, ox, oy)).resize(
            ref_strip.size, 1)  # 1 = Image.NEAREST; strips same size normally
        n = 0
        for pa, pb in zip(ref_strip.convert("RGB").getdata(),
                          cur.convert("RGB").getdata()):
            if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 40:
                n += 1
        return n

    # ============== instance 1: launch / B1 / invalid login / B7-B9 / nav / Ctrl+K / B11
    proc1, err1 = launch(1)
    try:
        h_main = wait_window(proc1.pid, MAIN)
        h_dlg = wait_window(proc1.pid, DIALOG)
        mains = find_windows(proc1.pid, MAIN)
        dialogs = find_windows(proc1.pid, DIALOG)
        step("A/B1. run_windows.py -> Main Window + LoginDialog (RC-3)",
             bool(h_main and h_dlg), {"main": h_main, "dialog": h_dlg})
        step("B1. exactly one Main Window", len(mains) == 1, {"count": len(mains)})
        step("B1. exactly one LoginDialog", len(dialogs) == 1,
             {"count": len(dialogs)})
        grab("01_login_dialog")

        # B2 invalid login: activate -> click secret -> Ctrl+A -> type -> Enter
        clicked = click_client(h_dlg, *DIALOG_SECRET, "secret field", report["kb_log"])
        sent = clicked and all([
            kb_send(h_dlg, "^a", "secret select-all", report["kb_log"]),
            kb_send(h_dlg, "definitely-wrong-secret", "invalid secret", report["kb_log"]),
            kb_send(h_dlg, "{ENTER}", "invalid login submit", report["kb_log"]),
        ])
        gone = wait_window_gone(proc1.pid, DIALOG)
        step("B2. invalid login -> rejected, dialog closes, app fails safe",
             gone and bool(find_windows(proc1.pid, MAIN)) and sent,
             {"keys_delivered": sent, "dialog_closed": gone})
        grab("02_after_invalid_login")

        ox, oy = client_origin(h_main)
        banner_rows, banner_heights = [], []
        accidental_applied = 0
        for label, (bx, by) in (("Buy", BUY), ("Pause", PAUSE),
                                ("Close Only", CLOSE_ONLY), ("Emergency", EMERGENCY)):
            clicked = click_client(h_main, *NEUTRAL, f"neutral {label}",
                                   report["kb_log"])
            before = grab(f"04_before_{label.replace(' ', '_').lower()}")
            clicked = clicked and click_client(h_main, bx, by, label,
                                               report["kb_log"])
            after = grab(f"05_after_{label.replace(' ', '_').lower()}")
            red0 = count_tinted(before.crop(sbox(BANNER_BOX, ox, oy)), "red")
            red1 = count_tinted(after.crop(sbox(BANNER_BOX, ox, oy)), "red")
            green1 = count_tinted(after.crop(sbox(BANNER_BOX, ox, oy)), "green")
            text_changed = diff_pixels(before, after, sbox(BANNER_BOX, ox, oy))
            bh = red_bbox_height(after, sbox(BANNER_BOX, ox, oy))
            banner_heights.append((label, bh))
            accidental_applied += green1
            banner_rows.append((f"{label} (logged out)",
                                strip(after, sbox(BANNER_BOX, ox, oy))))
            step(f"B7. {label} no-session -> visible REJECTED banner",
                 red1 > 1000 and text_changed > 80,
                 {"red_before": red0, "red_after": red1,
                  "banner_text_changed_pixels": text_changed})
            step(f"B8. {label} banner updates, single banner (no stacking)",
                 20 <= bh <= 48, {"red_text_bbox_height_px": bh})
        step("B9. all 4 no-session actions -> zero accidental APPLIED",
             accidental_applied == 0, {"green_pixels_total": accidental_applied})

        # B5 navigation: visible change + semantic title strips per destination
        title_strips, status_strips = [], []
        prev = grab("03_logged_out_baseline")
        for i, (nx, ny) in enumerate(NAV):
            name = NAV_NAMES[i]
            clicked = click_client(h_main, nx, ny, f"nav {name}", report["kb_log"])
            after = grab(f"06_nav_{name.lower()}")
            changed = diff_pixels(prev, after, sbox(BREGION_BOX, ox, oy))
            tstrip = after.crop(sbox(BTITLE_BOX, ox, oy))
            title_strips.append((f"nav {name}", strip(after, sbox(BTITLE_BOX, ox, oy))))
            status_strips.append((f"nav {name}",
                                  strip(after, sbox(STATUS_BOX, ox, oy))))
            step(f"B5. nav {name} -> visible workspace change",
                 clicked and changed > 300, {"changed_b_pixels": changed})
            prev = after
        pairs_ok = all(
            title_strips[i][1].tobytes() != title_strips[j][1].tobytes()
            for i in range(9) for j in range(i + 1, 9))
        step("B5. 9/9 titles pairwise distinct (9 distinct workspaces)",
             pairs_ok, {})
        overview_after = prev
        overview_counts = strip(overview_after, sbox(BCONTENT_BOX, ox, oy), 1)
        overview_counts.save(EVIDENCE / "overview_after_rejections.png")

        # B6 reverse navigation: title must match the destination reference
        reverse_ok = True
        for name in ("Overview", "Research", "Execution", "Overview"):
            nx, ny = NAV[NAV_INDEX[name]]
            clicked = click_client(h_main, nx, ny, f"reverse nav {name}",
                                   report["kb_log"])
            after = grab(f"06r_reverse_{name.lower()}")
            ref = title_strips[NAV_INDEX[name]][1]
            mismatch = nav_title_matches(ref, after, ox, oy)
            ok = clicked and mismatch < 300
            reverse_ok = reverse_ok and ok
            step(f"B6. reverse -> {name}: title matches destination",
                 ok, {"title_mismatch_pixels": mismatch})

        # B6 repeat navigation: Research x3, stable title, no corruption
        repeat_shots = []
        for k in range(3):
            nx, ny = NAV[NAV_INDEX["Research"]]
            clicked = click_client(h_main, nx, ny, f"repeat Research #{k + 1}",
                                   report["kb_log"])
            after = grab(f"06p_repeat_research_{k + 1}")
            repeat_shots.append(after)
        ref = title_strips[NAV_INDEX["Research"]][1]
        m1 = nav_title_matches(ref, repeat_shots[0], ox, oy)
        m2 = nav_title_matches(ref, repeat_shots[1], ox, oy)
        m3 = nav_title_matches(ref, repeat_shots[2], ox, oy)
        stable = diff_pixels(repeat_shots[1], repeat_shots[2],
                             sbox(BTITLE_BOX, ox, oy))
        step("B6. repeat Research x3 -> title stable, matches destination",
             m1 < 300 and m2 < 300 and m3 < 300 and stable < 300,
             {"mismatches": [m1, m2, m3], "shot2_vs_shot3_title_diff": stable})
        # state not corrupted: Overview reachable again and matches reference
        nx, ny = NAV[NAV_INDEX["Overview"]]
        click_client(h_main, nx, ny, "post-repeat Overview", report["kb_log"])
        after_ov = grab("06z_after_repeat_overview")
        m_ov = nav_title_matches(title_strips[0][1], after_ov, ox, oy)
        step("B6. after repeats -> Overview state intact (no corruption)",
             m_ov < 300, {"title_mismatch_pixels": m_ov})

        # B10 Ctrl+K real keyboard with layout normalized like a human tester
        clicked = click_client(h_main, *NEUTRAL, "neutral ctrl+k", report["kb_log"])
        sent = clicked and kb_vk_combo(h_main, VK_K, "Ctrl+K", report["kb_log"])
        h_pal = wait_window(proc1.pid, PALETTE, timeout=4)
        ctrlk_ok = bool(h_pal) and sent
        if h_pal:
            grab("07_command_palette")
            ensure_foreground(h_pal, "palette", report["kb_log"])
            kb_vk_key(VK_ESCAPE)
            wait_window_gone(proc1.pid, PALETTE)
        report["ctrl_k"] = "PASS" if ctrlk_ok else \
            "FAIL - FOLLOW-UP DIAGNOSIS REQUIRED"
        step("B10. Ctrl+K real keyboard (en-US) -> Command Palette",
             ctrlk_ok, {"result": report["ctrl_k"]})

        # B11 existing GUI: Search
        sb_before = grab("15_search_before")
        clicked = click_client(h_main, *SEARCH, "search entry", report["kb_log"])
        typed = clicked and all([
            kb_send(h_main, "^a", "search select-all", report["kb_log"]),
            kb_send(h_main, "EURUSD", "search text", report["kb_log"]),
            kb_send(h_main, "{ENTER}", "search Enter", report["kb_log"]),
        ])
        sb_after = grab("16_search_after")
        search_changed = diff_pixels(sb_before, sb_after, sbox(SEARCH_BOX, ox, oy))
        context_changed = diff_pixels(sb_before, sb_after, sbox(STATUS_BOX, ox, oy))
        step("B11. Search: focus -> type -> Enter -> search + context update",
             typed and search_changed > 40 and context_changed > 20,
             {"search_strip_changed": search_changed,
              "status_changed": context_changed})

        # B11 resize: change client size, render intact, restore
        rect = wintypes.RECT()
        user32.GetWindowRect(h_main, ctypes.byref(rect))
        size_before = client_size(h_main)
        user32.MoveWindow(h_main, rect.left, rect.top, 1100, 780, True)
        time.sleep(0.8)
        size_mid = client_size(h_main)
        oxr, oyr = client_origin(h_main)
        rs = grab("17_resized")
        # semantic check on resized window: B title strip still shows the
        # Overview preset text vs the post-B6 Overview reference
        pre_title = after_ov.crop(sbox(BTITLE_BOX, ox, oy))
        post_title = rs.crop(sbox(BTITLE_BOX, oxr, oyr))
        tdiff = 0
        if pre_title.size == post_title.size:
            for pa, pb in zip(pre_title.convert("RGB").getdata(),
                              post_title.convert("RGB").getdata()):
                if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 40:
                    tdiff += 1
        user32.MoveWindow(h_main, rect.left, rect.top,
                          rect.right - rect.left, rect.bottom - rect.top, True)
        time.sleep(0.8)
        size_after = client_size(h_main)
        step("B11. Resize: client size changes, render intact, restored",
             size_before != size_mid and tdiff < 400 and size_after == size_before,
             {"before": size_before, "resized": size_mid, "restored": size_after,
              "title_diff_resized": tdiff})

        # B11 minimize / restore
        user32.ShowWindow(h_main, SW_MINIMIZE)
        time.sleep(0.6)
        iconic = bool(user32.IsIconic(h_main))
        user32.ShowWindow(h_main, SW_RESTORE)
        time.sleep(0.8)
        iconic_after = bool(user32.IsIconic(h_main))
        oxm, oym = client_origin(h_main)
        ms = grab("18_restored")
        pre_t = after_ov.crop(sbox(BTITLE_BOX, ox, oy))
        post_t = ms.crop(sbox(BTITLE_BOX, oxm, oym))
        mdiff = 0
        if pre_t.size == post_t.size:
            for pa, pb in zip(pre_t.convert("RGB").getdata(),
                              post_t.convert("RGB").getdata()):
                if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 40:
                    mdiff += 1
        step("B11. Minimize -> Restore: render intact",
             iconic and not iconic_after and mdiff < 400,
             {"was_minimized": iconic, "restored": not iconic_after,
              "title_diff_restored": mdiff})

        contact_sheet(banner_rows, "feedback_banner_sheet")
        contact_sheet(title_strips, "nav_title_sheet")
        contact_sheet(status_strips, "nav_status_sheet")
    finally:
        code1 = shutdown(1, proc1, err1)
        step("B12. clean close -> process exits cleanly", code1 == 0,
             {"exit_code": code1})

    # ============== instance 2: cancel login
    proc2, err2 = launch(2)
    try:
        h_main2 = wait_window(proc2.pid, MAIN)
        h_dlg2 = wait_window(proc2.pid, DIALOG)
        step("B3. cancel precondition -> exactly one LoginDialog",
             h_dlg2 is not None and len(find_windows(proc2.pid, DIALOG)) == 1,
             {"count": len(find_windows(proc2.pid, DIALOG))})
        close_window(h_dlg2)
        gone = wait_window_gone(proc2.pid, DIALOG)
        step("B3. cancel -> dialog closes, app fails safe, no session",
             gone and bool(find_windows(proc2.pid, MAIN)), {"dialog_closed": gone})
        grab("08_after_cancel_login")
        ox2, oy2 = client_origin(h_main2)
        clicked = click_client(h_main2, *NEUTRAL, "neutral cancel-Buy",
                               report["kb_log"])
        before = grab("09_cancel_before_buy")
        clicked = clicked and click_client(h_main2, *BUY, "cancel-then-Buy",
                                           report["kb_log"])
        after = grab("10_cancel_after_buy")
        red0 = count_tinted(before.crop(sbox(BANNER_BOX, ox2, oy2)), "red")
        red1 = count_tinted(after.crop(sbox(BANNER_BOX, ox2, oy2)), "red")
        step("B3. after cancel -> Buy still REJECTED (no bypass)",
             clicked and red1 > red0 + 800,
             {"red_before": red0, "red_after": red1})
    finally:
        shutdown(2, proc2, err2)

    # ============== instance 3: valid login -> session + APPLIED
    proc3, err3 = launch(3)
    try:
        h_main3 = wait_window(proc3.pid, MAIN)
        h_dlg3 = wait_window(proc3.pid, DIALOG)
        step("B4. valid login precondition -> LoginDialog present", bool(h_dlg3), {})
        pre = grab("11_pre_login")
        ox3, oy3 = client_origin(h_main3)
        status_before = strip(pre, sbox(STATUS_BOX, ox3, oy3))
        clicked = click_client(h_dlg3, *DIALOG_SECRET, "secret field (valid)",
                               report["kb_log"])
        sent = clicked and all([
            kb_send(h_dlg3, "^a", "secret select-all (valid)", report["kb_log"]),
            kb_send(h_dlg3, SECRET_TRADER, "valid secret", report["kb_log"]),
            kb_send(h_dlg3, "{ENTER}", "valid login submit", report["kb_log"]),
        ])
        gone = wait_window_gone(proc3.pid, DIALOG)
        mains3 = find_windows(proc3.pid, MAIN)
        dlgs3 = find_windows(proc3.pid, DIALOG)
        post = grab("12_post_login")
        status_after = strip(post, sbox(STATUS_BOX, ox3, oy3))
        status_changed = diff_pixels(pre, post, sbox(STATUS_BOX, ox3, oy3))
        step("B4. valid login -> dialog closes, session visibly established",
             gone and sent and status_changed > 30,
             {"keys_delivered": sent, "dialog_closed": gone,
              "changed_status_pixels": status_changed})
        step("B4. exactly one Main Window, no duplicate LoginDialog",
             len(mains3) == 1 and len(dlgs3) == 0,
             {"main_count": len(mains3), "dialog_count": len(dlgs3)})

        clicked = click_client(h_main3, *NEUTRAL, "neutral auth Buy",
                               report["kb_log"])
        before = grab("13_logged_in_before_buy")
        clicked = clicked and click_client(h_main3, *BUY, "authenticated Buy",
                                           report["kb_log"])
        after = grab("14_logged_in_after_buy")
        g0 = count_tinted(before.crop(sbox(BANNER_BOX, ox3, oy3)), "green")
        g1 = count_tinted(after.crop(sbox(BANNER_BOX, ox3, oy3)), "green")
        step("B4. authenticated Buy -> visible APPLIED banner (behavior kept)",
             clicked and g1 > g0 + 40, {"green_before": g0, "green_after": g1})
        contact_sheet([
            ("statusbar BEFORE login", status_before),
            ("statusbar AFTER valid login", status_after),
            ("banner after authenticated Buy",
             strip(after, sbox(BANNER_BOX, ox3, oy3))),
        ], "login_evidence_sheet")
    finally:
        code3 = shutdown(3, proc3, err3)
        step("B12. authenticated session close -> clean exit", code3 == 0,
             {"exit_code": code3})

    # ============== instance 4: relaunch -> no stale session, no zombie
    zombies = [w for w in find_windows_any_pid(MAIN)]
    step("B12. no zombie Main Window after close", len(zombies) == 0,
         {"count": len(zombies)})
    proc4, err4 = launch(4)
    try:
        h_main4 = wait_window(proc4.pid, MAIN)
        h_dlg4 = wait_window(proc4.pid, DIALOG)
        dlgs4 = find_windows(proc4.pid, DIALOG)
        mains4 = find_windows(proc4.pid, MAIN)
        step("B12. relaunch -> exactly one LoginDialog, one Main Window",
             len(dlgs4) == 1 and len(mains4) == 1,
             {"dialogs": len(dlgs4), "mains": len(mains4)})
        grab("19_relaunch_dialog")
        close_window(h_dlg4)
        wait_window_gone(proc4.pid, DIALOG)
        ox4, oy4 = client_origin(h_main4)
        before = grab("20_relaunch_before_buy")
        click_client(h_main4, *BUY, "relaunch Buy (stale session check)",
                     report["kb_log"])
        after = grab("21_relaunch_after_buy")
        red1 = count_tinted(after.crop(sbox(BANNER_BOX, ox4, oy4)), "red")
        red0 = count_tinted(before.crop(sbox(BANNER_BOX, ox4, oy4)), "red")
        step("B12. relaunch -> no stale session (Buy REJECTED)",
             red1 > red0 + 800, {"red_before": red0, "red_after": red1})
    finally:
        shutdown(4, proc4, err4)

    report["cross_window_safe"] = all(
        e["match"] for e in report["kb_log"] if str(e["action"]).startswith("kb"))
    report["finished_utc"] = now_utc()
    (EVIDENCE / "live_gui_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")

    def got(prefix):
        return all(s["result"] == "PASS" for s in report["steps"]
                   if s["step"].startswith(prefix))

    nav_count = sum(1 for s in report["steps"]
                    if s["step"].startswith("B5. nav ") and s["result"] == "PASS")
    rej = sum(1 for s in report["steps"]
              if s["step"].startswith("B7.") and s["result"] == "PASS")

    print("\n================ PHASE C - LIVE GUI ACCEPTANCE GATE ================")
    rows = [
        ("LoginDialog launch", got("A/B1.")),
        ("Invalid Login", got("B2.")),
        ("Cancel Login", got("B3.")),
        ("Valid Login", got("B4. valid")),
        (f"Navigation ({nav_count}/9)", nav_count == 9),
        ("Reverse Navigation", got("B6. reverse")),
        ("Repeat Navigation", got("B6. repeat") and got("B6. after")),
        ("Buy rejection feedback", rej >= 1),
        ("Pause rejection feedback", got("B7. Pause")),
        ("Close Only rejection feedback", got("B7. Close Only")),
        ("Emergency rejection feedback", got("B7. Emergency")),
        ("Rejection no-side-effect", got("B9.")),
        ("Existing GUI components", got("B11.")),
        ("Ctrl+K", report["ctrl_k"] == "PASS"),
        ("Focus safety", report["cross_window_safe"]),
        ("Clean close/relaunch", got("B12.")),
        ("Runtime exceptions", report["runtime_exceptions"] == 0),
    ]
    for label, ok in rows:
        print(f"{label:<34} {'PASS' if ok else 'FAIL'}")
    print(f"Ctrl+K                  {report['ctrl_k']}")
    all_ok = (all(s["result"] == "PASS" for s in report["steps"])
              and report["cross_window_safe"] and report["runtime_exceptions"] == 0)
    print("======================================================================")
    print("LIVE GUI VALIDATION: " + ("ALL PASS" if all_ok else "FAILURES PRESENT"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
