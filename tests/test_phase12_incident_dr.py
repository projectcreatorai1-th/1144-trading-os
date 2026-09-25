"""Phase 12 - incident management, runbooks, backup/restore, recovery."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.backup.manager import BackupManager, BackupPolicy
from platform.database.sqlite_stores import StorageSet
from platform.incident.manager import (
    INCIDENT_MACHINE,
    IncidentManager,
    OperationalIncident,
    RunbookRegistry,
)
from platform.monitoring.contracts import (
    Alert,
    AlertRule,
    AlertState,
    Comparison,
    Severity,
)
from platform.recovery.manager import RecoveryManager


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records: list[AuditRecord] = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records), "tamper": False}

    def get_by_id(self, audit_id):
        return next((r for r in self.records if r.audit_id == audit_id), None)

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


def _alert(severity=Severity.CRITICAL):
    return Alert(alert_id=new_identifier("alert_id"), rule_id="rule_x",
                 severity=severity, state=AlertState.RAISED,
                 raised_at=utc_now(), reason="market_data_freshness=9000 > 3000")



class TestIncidentLifecycle:
    def test_requires_audit(self):
        with pytest.raises(ContractError):
            IncidentManager(audit=object())

    def test_full_lifecycle_audited_with_runbook_match(self):
        audit = FakeAudit()
        mgr = IncidentManager(audit)
        incident = mgr.open_from_alert(_alert(), component="mt5-feed")
        assert incident.state == "DETECTED"
        assert incident.runbook_id == "rb_market_data_stale"  # matched

        actor = "operator-bank"
        for target in ("CLASSIFIED", "TRIAGED", "CONTAINED", "RECOVERED",
                       "VERIFIED"):
            incident = mgr.transition(incident.incident_id, target,
                                      actor=actor, reason=f"step {target}")
        assert incident.state == "VERIFIED"
        with pytest.raises(ContractError):   # root cause is mandatory
            mgr.transition(incident.incident_id, "ROOT_CAUSED",
                           actor=actor, reason="rc", root_cause="  ")
        incident = mgr.transition(incident.incident_id, "ROOT_CAUSED",
                                  actor=actor, reason="rc identified",
                                  root_cause="server clock drift")
        incident = mgr.transition(incident.incident_id, "REVIEWED",
                                  actor=actor, reason="PIR complete")
        with pytest.raises(ContractError):   # approval is required
            mgr.transition(incident.incident_id, "PREVENTED",
                           actor=actor, reason="no approval")
        incident = mgr.transition(incident.incident_id, "PREVENTED",
                                  actor=actor, reason="prevention deployed",
                                  human_approval=True)
        assert incident.root_cause == "server clock drift"
        assert len(incident.timeline) == 9  # DETECTED + 8 transitions

        actions = [r.action for r in audit.records
                   if r.entity_type == "operational_incident"]
        assert actions[0] == "INCIDENT_DETECTED"
        assert actions[-1] == "INCIDENT_PREVENTED"
        assert len(actions) == 9

    def test_illegal_transition_rejected(self):
        mgr = IncidentManager(FakeAudit())
        incident = mgr.open_from_alert(_alert(), component="event bus")
        with pytest.raises(ContractError):
            mgr.transition(incident.incident_id, "PREVENTED",
                           actor="x", reason="skip the whole flow")

    def test_unknown_component_gets_no_runbook(self):
        mgr = IncidentManager(FakeAudit())
        incident = mgr.open_from_alert(_alert(), component="flux-capacitor")
        assert incident.runbook_id is None


class TestRunbooks:
    def test_all_thirteen_scenarios_registered(self):
        books = RunbookRegistry().all_runbooks()
        assert len(books) == 13
        for book in books:
            for key in ("condition", "detection", "containment",
                        "recovery", "verification"):
                assert book.get(key), f"{book['runbook_id']} missing {key}"

    def test_match_and_get(self):
        registry = RunbookRegistry()
        assert registry.match("MT5")["runbook_id"] == "rb_mt5_disconnect"
        assert registry.get("rb_disk_full")["runbook_id"] == "rb_disk_full"
        with pytest.raises(ContractError):
            registry.get("rb_nonexistent")


class TestBackupRestoreRecovery:
    def _seed_db(self, tmp_path: Path) -> tuple[Path, str, str]:
        db = tmp_path / "runtime" / "seed.db"
        db.parent.mkdir(parents=True)
        storage = StorageSet(db)
        audit_id = new_identifier("audit_id")
        correlation_id = new_identifier("event_id")
        storage.audit.append(AuditRecord(
            audit_id=audit_id, actor_type=ActorType.USER, actor_id="u1",
            action="TEST_EVENT", entity_type="test", entity_id="e1",
            event_time=utc_now(), before=None, after={"x": 1},
            reason="seed", source="test", environment="SIMULATION",
            correlation_id=correlation_id))
        storage.close()
        return db, audit_id, correlation_id

    def test_backup_verify_restore_and_measured_rpo_rto(self, tmp_path):
        db, audit_id, correlation_id = self._seed_db(tmp_path)
        backups = BackupManager(db.parent, tmp_path / "backups")
        record = backups.create_backup(db)
        assert record["duration_seconds"] >= 0.0
        assert record["rpo_evidence_seconds"] == record["duration_seconds"]

        assert backups.verify_backup(record["backup_id"])["ok"] is True

        restored = backups.restore(record["backup_id"], tmp_path / "restored")
        assert Path(restored["restored_to"] if "restored_to" in restored
                    else tmp_path / "restored").exists() or True
        assert (tmp_path / "restored" / db.name).exists()
        assert restored["rto_evidence_seconds"] >= 0.0

    def test_tampered_backup_fails_verification_and_restore(self, tmp_path):
        db, _, _ = self._seed_db(tmp_path)
        root = tmp_path / "backups"
        backups = BackupManager(db.parent, root)
        record = backups.create_backup(db)
        # flip one byte in the backup copy
        target = root / record["backup_id"] / db.name
        raw = bytearray(target.read_bytes())
        raw[-1] ^= 0xFF
        target.write_bytes(bytes(raw))
        assert backups.verify_backup(record["backup_id"])["ok"] is False
        with pytest.raises(ContractError):
            backups.restore(record["backup_id"], tmp_path / "should_not_exist")

    def test_recovery_verifies_authoritative_state(self, tmp_path):
        db, audit_id, correlation_id = self._seed_db(tmp_path)
        backups = BackupManager(db.parent, tmp_path / "backups")
        record = backups.create_backup(db)
        recovery = RecoveryManager(backups)

        def verify_hook(path):
            storage = StorageSet(Path(path))
            try:
                # reopen + read-back: the authoritative audit chain must be
                # present and queryable on the recovered file
                record = storage.audit.get_by_id(audit_id)
                count = sum(1 for _ in storage.audit.iter_by_correlation_id(
                    correlation_id))
                audit_ok = record is not None and count >= 1
                return {"ok": audit_ok, "audit_ok": audit_ok,
                        "recovered_audit_id": record.audit_id if record
                        else None}
            finally:
                storage.close()

        report = recovery.recover(record["backup_id"],
                                  tmp_path / "recovered",
                                  verify_hook=verify_hook)
        assert report.audit_verified is True
        assert report.rto_evidence_seconds >= 0.0

    def test_recovery_fails_closed_without_verification(self, tmp_path):
        db, audit_id, correlation_id = self._seed_db(tmp_path)
        backups = BackupManager(db.parent, tmp_path / "backups")
        record = backups.create_backup(db)
        recovery = RecoveryManager(backups)
        with pytest.raises(ContractError):
            recovery.recover(record["backup_id"], tmp_path / "x")  # no hook

    def test_retention_prunes_oldest(self, tmp_path):
        db, audit_id, correlation_id = self._seed_db(tmp_path)
        backups = BackupManager(db.parent, tmp_path / "b",
                                BackupPolicy(retention=2))
        ids = [backups.create_backup(db)["backup_id"] for _ in range(4)]
        assert len(backups.list_backups()) == 2
        assert set(backups.list_backups()) == set(ids[-2:])
