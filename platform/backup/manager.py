"""Backup + restore with integrity verification (platform.backup, P12).

Backs up the runtime storage set (one SQLite file) into versioned backup
directories with a SHA-256 manifest; restore verifies every hash before a
single byte is written to the target (fail closed). Durations are MEASURED
and recorded as RPO/RTO evidence - never declared without measurement.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now

CONTRACT_VERSION = "1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupPolicy:
    retention: int = 10          # keep the N most recent backups
    include_pattern: str = "*.db"

    def validate(self) -> None:
        if self.retention < 1:
            raise ContractError("retention must be >= 1",
                                location="backup.policy", rule_id="BKP-001")


class BackupManager:
    def __init__(self, runtime_dir: Path, backup_root: Path,
                 policy: BackupPolicy | None = None) -> None:
        self._runtime = Path(runtime_dir)
        self._root = Path(backup_root)
        self.policy = policy or BackupPolicy()
        self.policy.validate()

    # ------------------------------------------------------------------ #
    def create_backup(self, source_db: Path | None = None,
                      *, at: datetime | None = None) -> dict[str, Any]:
        """Copy the storage set + manifest. RPO evidence: the measured
        backup duration bounds the data-loss window for this snapshot."""
        moment = at or utc_now()
        started = time.perf_counter()
        src = Path(source_db) if source_db else None
        candidates = [src] if src else sorted(self._runtime.glob(
            self.policy.include_pattern))
        if not candidates:
            raise ContractError(
                f"no storage files match {self.policy.include_pattern!r} "
                f"under {self._runtime}",
                location="backup.create", rule_id="BKP-002")
        backup_id = new_identifier("backup_id")
        target_dir = self._root / backup_id
        target_dir.mkdir(parents=True, exist_ok=False)
        manifest: dict[str, str] = {}
        try:
            for file_path in candidates:
                dest = target_dir / file_path.name
                shutil.copy2(file_path, dest)
                manifest[file_path.name] = _sha256(dest)
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
        duration_s = time.perf_counter() - started
        record = {
            "backup_id": backup_id,
            "created_at": moment.isoformat(),
            "files": manifest,
            "duration_seconds": round(duration_s, 4),
            "rpo_evidence_seconds": round(duration_s, 4),
        }
        (target_dir / "manifest.json").write_text(
            __import__("json").dumps(record, indent=2), encoding="utf-8")
        self._enforce_retention()
        return record

    def _enforce_retention(self) -> None:
        backups = sorted(
            (d for d in self._root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True)
        for old in backups[self.policy.retention:]:
            shutil.rmtree(old, ignore_errors=True)

    def verify_backup(self, backup_id: str) -> dict[str, Any]:
        directory = self._root / backup_id
        record = self._load_manifest(directory)
        results = {}
        for name, expected in record["files"].items():
            path = directory / name
            if not path.exists():
                results[name] = "MISSING"
            elif _sha256(path) != expected:
                results[name] = "HASH_MISMATCH"
            else:
                results[name] = "OK"
        ok = all(v == "OK" for v in results.values())
        return {"backup_id": backup_id, "ok": ok, "files": results}

    def restore(self, backup_id: str, target_dir: Path) -> dict[str, Any]:
        """Restore ONLY after full hash verification; the measured duration
        is the RTO evidence for this restore path."""
        directory = self._root / backup_id
        record = self._load_manifest(directory)
        verification = self.verify_backup(backup_id)
        if not verification["ok"]:
            raise ContractError(
                f"backup {backup_id} failed integrity verification - "
                "refusing to restore (fail closed)",
                location="backup.restore", rule_id="BKP-003",
                details={"verification": verification["files"]})
        started = time.perf_counter()
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in record["files"]:
            shutil.copy2(directory / name, target_dir / name)
        duration_s = time.perf_counter() - started
        return {"backup_id": backup_id, "restored_to": str(target_dir),
                "files": list(record["files"]),
                "duration_seconds": round(duration_s, 4),
                "rto_evidence_seconds": round(duration_s, 4)}

    def list_backups(self) -> list[str]:
        return sorted(d.name for d in self._root.iterdir() if d.is_dir())

    @staticmethod
    def _load_manifest(directory: Path) -> dict:
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise ContractError(f"no manifest in {directory}",
                                location="backup.manifest", rule_id="BKP-003")
        import json
        return json.loads(manifest_path.read_text(encoding="utf-8"))
