"""BASELINE REPAIR regression gates (test tooling, not production).

Runs the validation chain of the final-validation command (Phases D-G) and
writes docs/phase-9/evidence/gui-repair-live/regression_gates.json.

NOTE on suite names: between sessions the Phase 9 test files were externally
restructured (test_phase9_desktop_core.py + test_phase9_shell.py merged into
tests/test_phase9_core.py; test_phase9_e2e.py -> tests/test_phase9_invariants_
e2e.py). Gates map to the ACTUAL files present; the restructure is reported,
not hidden. Full pytest remains the authoritative gate.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "phase-9" / "evidence" / "gui-repair-live" / "regression_gates.json"


def run(name, cmd):
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600)
    tail = [line for line in proc.stdout.strip().splitlines()[-8:]]
    ok = proc.returncode == 0
    entry = {"gate": name, "cmd": " ".join(cmd), "exit_code": proc.returncode,
             "ok": ok, "seconds": round(time.monotonic() - started, 1),
             "tail": tail}
    print(f"[{'PASS' if ok else 'FAIL'}] {name} exit={proc.returncode} "
          f"({entry['seconds']}s)")
    for line in tail:
        print("      " + line)
    return entry


def main() -> int:
    gates = [
        ("D1 Phase9 core tests (incl. shell; restructured)", [
            sys.executable, "-m", "pytest", "-q", "tests/test_phase9_core.py"]),
        ("D2 Phase9 invariants/E2E", [
            sys.executable, "-m", "pytest", "-q",
            "tests/test_phase9_invariants_e2e.py"]),
        ("D3 GUI Repair tests", [
            sys.executable, "-m", "pytest", "-q",
            "tests/test_phase9_gui_repair.py"]),
        ("D4 Full pytest", [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]),
        ("E Architecture Validator", [
            sys.executable, "-m", "architecture.validator"]),
        ("F Windows Build (relocated into run_windows.py --verify-build)", [
            sys.executable, "ui/desktop/run_windows.py", "--verify-build"]),
        ("G1 LIVE Safety (relocated: Phase 10 core LIVE-refusal tests)", [
            sys.executable, "-m", "pytest", "-q",
            "tests/test_phase10_core.py", "-k", "live"]),
    ]
    results = [run(name, cmd) for name, cmd in gates]
    results.append({
        "gate": "G2 Security/Quality Audit",
        "cmd": "tools/security_quality_audit.py (deleted externally)",
        "exit_code": None, "ok": False, "seconds": 0.0,
        "tail": ["BLOCKED: standalone audit tool deleted externally; searched "
                 "platform/backup, Releases, Recycle Bin ($R scan), Desktop/"
                 "Documents/Downloads, D:/F: roots, all shortcut targets - not "
                 "found; NOT recreated from memory (no fake tools). Partial "
                 "static coverage: SEC-* rules inside the passing validator."],
    })
    print("[BLOCKED] G2 Security/Quality Audit - tool deleted externally")
    blocked = [g for g in results if not g["ok"]]
    report = {
        "run": "BASELINE REPAIR regression gates (final validation command)",
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "suite_restructure_note": (
            "Phase 9 test files were externally restructured between sessions; "
            "gates target the actual files present. Full pytest (D4) is the "
            "authoritative regression gate."),
        "gates": results,
        "all_pass": all(g["ok"] for g in results),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print()
    if not blocked:
        print("ALL GATES PASS")
    else:
        print("GATE FAILURES/BLOCKS PRESENT: " +
              ", ".join(g["gate"] for g in blocked))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
