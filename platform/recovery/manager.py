"""Recovery executor (platform.recovery, Phase 12).

Recovers authoritative state from a verified backup: restore -> reopen the
storage set on the restored file -> audit-chain verification -> post-
recovery report. If ANY verification step fails the recovery fails closed
and the original state is left untouched (INV-REC-001: recovery preserves
authoritative state - it never invents one).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from architecture.contracts.errors import ContractError
from platform.backup.manager import BackupManager

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class RecoveryReport:
    backup_id: str
    restored_path: str
    audit_verified: bool
    post_recovery_state: dict
    rto_evidence_seconds: float


class RecoveryManager:
    def __init__(self, backups: BackupManager) -> None:
        self._backups = backups

    def recover(self, backup_id: str, target_dir: Path,
                *, verify_hook=None) -> RecoveryReport:
        """Restore + verify. `verify_hook(restored_db_path)` opens the
        storage on the recovered file and returns a state summary dict
        (e.g. audit.verify() + store counts). Recovery only succeeds when
        the hook reports success - a hook failure fails the recovery."""
        restored = self._backups.restore(backup_id, target_dir)
        db_files = [f for f in restored["files"] if f.endswith(".db")]
        if len(db_files) != 1:
            raise ContractError(
                "recovery expects exactly one storage file in the backup",
                location="recovery.recover", rule_id="REC-001",
                details={"files": restored["files"]})
        db_path = target_dir / db_files[0]
        if verify_hook is None:
            raise ContractError(
                "recovery requires a verification hook (no verification "
                "= no recovery)",
                location="recovery.recover", rule_id="REC-002")
        summary = verify_hook(db_path)
        if not isinstance(summary, dict) or not summary.get("ok", False):
            raise ContractError(
                f"post-recovery verification failed: {summary!r} - "
                "recovery not acknowledged",
                location="recovery.recover", rule_id="REC-002")
        return RecoveryReport(
            backup_id=backup_id, restored_path=str(db_path),
            audit_verified=bool(summary.get("audit_ok", False)),
            post_recovery_state=summary,
            rto_evidence_seconds=restored["rto_evidence_seconds"])
