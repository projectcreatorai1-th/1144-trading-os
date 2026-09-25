"""Ledger contract tests (SECTION 15) - including immutability failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractValidationError, IdentifierValidationError
from architecture.contracts.identifiers import new_identifier
from core.ledger.contracts import LedgerType
from tests.factories import make_ledger_entry


class TestLedgerContract:
    def test_valid_entry(self):
        make_ledger_entry().validate()

    def test_all_ledger_types_registered(self):
        assert {t.value for t in LedgerType} == {
            "ORDER", "EXECUTION", "POSITION", "CASH", "FEE", "COMMISSION",
            "SWAP", "FUNDING", "MARGIN", "ADJUSTMENT", "TRANSFER",
        }

    def test_adjustment_entry_linked(self):
        raw = make_ledger_entry()
        adjustment = raw.adjustment_for(
            ledger_entry_id=new_identifier("ledger_entry_id"),
            amount=Decimal("-1.20"),
            reason="commission correction",
        )
        adjustment.validate()
        assert adjustment.adjusts_entry_id == raw.ledger_entry_id
        assert adjustment.entry_type is LedgerType.ADJUSTMENT

    def test_reversal_entry_negates_amount(self):
        raw = make_ledger_entry(amount=Decimal("100.00"))
        reversal = raw.reversal_of(
            ledger_entry_id=new_identifier("ledger_entry_id"), reason="wrong booking"
        )
        reversal.validate()
        assert reversal.amount == Decimal("-100.00")
        assert reversal.reverses_entry_id == raw.ledger_entry_id

    def test_raw_entry_unchanged_after_correction(self):
        raw = make_ledger_entry()
        raw.adjustment_for(
            ledger_entry_id=new_identifier("ledger_entry_id"),
            amount=Decimal("-1.20"),
            reason="correction",
        )
        assert raw.amount == Decimal("-2.40")
        assert raw.adjusts_entry_id is None


class TestLedgerFailures:
    def test_entry_without_trace_reference_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_ledger_entry(order_id=None).validate()
        assert excinfo.value.rule_id == "TRACE-001"

    def test_invalid_entry_type_rejected(self):
        with pytest.raises(ContractValidationError):
            make_ledger_entry(entry_type="TAX").validate()

    def test_invalid_trace_id_format_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_ledger_entry(order_id="order-1").validate()

    def test_missing_correlation_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_ledger_entry(correlation_id="chain-1").validate()

    def test_adjustment_without_reference_rejected(self):
        with pytest.raises(ContractValidationError):
            make_ledger_entry(
                entry_type=LedgerType.ADJUSTMENT,
                ledger_entry_id=new_identifier("ledger_entry_id"),
            ).validate()

    def test_adjusts_and_reverses_mutually_exclusive(self):
        raw = make_ledger_entry()
        with pytest.raises(ContractValidationError):
            make_ledger_entry(
                ledger_entry_id=new_identifier("ledger_entry_id"),
                adjusts_entry_id=raw.ledger_entry_id,
                reverses_entry_id=raw.ledger_entry_id,
            ).validate()

    def test_transfer_cannot_be_correction(self):
        raw = make_ledger_entry()
        with pytest.raises(ContractValidationError):
            make_ledger_entry(
                entry_type=LedgerType.TRANSFER,
                adjusts_entry_id=raw.ledger_entry_id,
            ).validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_ledger_entry(environment="CRYPTO").validate()

    def test_raw_entry_immutable(self):
        entry = make_ledger_entry()
        with pytest.raises(FrozenInstanceError):
            entry.amount = Decimal("0")
