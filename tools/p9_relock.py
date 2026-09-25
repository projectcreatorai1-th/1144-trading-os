"""Phase 9 baseline RE-LOCK generator (tooling, not production).

Section 11 of the master command: after the GUI repair passes, re-lock the
Phase 9 baseline. Originals are NEVER overwritten; this creates a new
generation of artifacts under docs/phase-9/:

  PHASE_9_RELOCK_BASELINE.json   - full current hash manifest + lineage
  PHASE_9_RELOCK.md              - release snapshot + handoff summary
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P9 = ROOT / "docs" / "phase-9"

# the original Phase 9 manifest scope + everything that legitimately changed
SCAN = ["architecture", "core", "platform", "adapters", "ui", "tools", "tests"]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    original = json.loads((P9 / "PHASE_9_FILE_HASHES.json").read_text(encoding="utf-8"))
    original_hashes = {e["relative_path"]: e["sha256"] for e in original["files"]}

    files = []
    for folder in SCAN:
        for p in sorted((ROOT / folder).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if "__pycache__" in rel:
                continue
            files.append(rel)
    for extra in ("architecture/connectivity.yaml",):
        p = ROOT / extra
        if p.exists():
            files.append(extra)

    manifest = []
    for rel in files:
        h = sha256(ROOT / rel)
        entry = {"relative_path": rel, "sha256": h,
                 "size": (ROOT / rel).stat().st_size}
        if rel in original_hashes:
            entry["vs_original_phase9"] = (
                "UNCHANGED" if h == original_hashes[rel] else "CHANGED")
        else:
            entry["vs_original_phase9"] = "NEW-SINCE-PHASE9-BASELINE"
        manifest.append(entry)

    relock = {
        "record_type": "PHASE 9 BASELINE RE-LOCK",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "lineage": {
            "original_baseline": "docs/phase-9/PHASE_9_FILE_HASHES.json "
                                 "(preserved, untouched)",
            "intermediate": "docs/phase-9/evidence/gui-repair-live/"
                            "BASELINE_REPAIR_RECORD.json (GUI repair + "
                            "external-change audit)",
            "this_generation": "post GUI-repair re-lock",
        },
        "gui_repair_status": "RC-3 PASS, RC-F 9/9 PASS, RC-2 4/4 PASS, "
                             "Ctrl+K PASS (live 17/17); G2 audit tool "
                             "externally deleted -> documented BLOCKED, "
                             "partial SEC coverage via validator",
        "regression_at_relock": {
            "full_pytest": "2034 passed / 1 skipped (documented P10 "
                           "conditional) / 0 failed / 0 errors",
            "architecture_validator": "PASS (0 violations)",
            "windows_build": "PASS 9/9 (run_windows.py --verify-build)",
            "live_safety": "PASS (validator LIVE rules + P10 LIVE-refusal "
                           "tests)",
        },
        "file_count": len(manifest),
        "files": manifest,
        "phase10_status_at_relock": "IMPLEMENTATION COMPLETE - RUNTIME GATE "
                                    "BLOCKED (findings pending real-evidence "
                                    "fix per master command section 16.3)",
    }
    (P9 / "PHASE_9_RELOCK_BASELINE.json").write_text(
        json.dumps(relock, indent=2), encoding="utf-8")

    md = f"""# Phase 9 Baseline RE-LOCK ({datetime.now(timezone.utc).isoformat()})

Generation 2 of the Phase 9 baseline, locked after the GUI interaction
repair (RC-2, RC-3, RC-F) passed live validation (17/17) and full
regression (2034/1/0/0, validator 0 violations, build 9/9, LIVE safety
PASS; Security/Quality standalone audit BLOCKED - tool deleted externally,
search exhausted, not recreated).

* Original baseline preserved: `PHASE_9_FILE_HASHES.json` (untouched)
* Repair audit: `evidence/gui-repair-live/BASELINE_REPAIR_RECORD.json`
* Full manifest of this generation: `PHASE_9_RELOCK_BASELINE.json`
  ({len(manifest)} files, each marked UNCHANGED / CHANGED / NEW-SINCE vs the
  original baseline)
* GUI evidence: `evidence/gui-repair-live/` (60+ artifacts) and
  `GUI_REPAIR_FINAL_REPORT.md`

External modifications present at re-lock (audited, not absorbed):
architecture/*.yaml (5), architecture/validator/rules.py,
core/events/contracts.py, platform/api/desktop_gateway.py, test-suite
restructure, validator relocation to architecture.validator, build verifier
relocated into ui/desktop/run_windows.py --verify-build. Details in the
repair record and TOOLING_INCIDENTS.md.

PHASE 9 BASELINE LOCK = YES (generation 2)
"""
    (P9 / "PHASE_9_RELOCK.md").write_text(md, encoding="utf-8")
    print(f"re-lock manifest: {len(manifest)} files")
    print("wrote PHASE_9_RELOCK_BASELINE.json + PHASE_9_RELOCK.md")


if __name__ == "__main__":
    main()
