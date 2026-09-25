"""Phase 2 money policy tests (SECTIONS 40/41)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractValidationError
from core.ledger.money import (
    Money,
    parse_decimal,
    precision_for,
    registered_currencies,
    rounding_mode_for,
)


class TestCurrencyRegistry:
    def test_registered_currencies(self):
        assert set(registered_currencies()) >= {"USD", "EUR", "JPY", "THB", "XAU"}

    def test_precision(self):
        assert precision_for("USD") == 2
        assert precision_for("JPY") == 0
        assert precision_for("XAU") == 2

    def test_unknown_currency_rejected(self):
        with pytest.raises(ContractValidationError):
            precision_for("BTC")

    def test_default_rounding_registered(self):
        assert rounding_mode_for("USD") == "ROUND_HALF_EVEN"


class TestCanonicalDecimal:
    def test_parse_from_string_and_int(self):
        assert parse_decimal("10.25", location="t") == Decimal("10.25")
        assert parse_decimal(7, location="t") == Decimal(7)

    def test_binary_float_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            parse_decimal(0.1, location="t")
        assert excinfo.value.rule_id == "LEDGER-004"

    def test_bool_rejected(self):
        with pytest.raises(ContractValidationError):
            parse_decimal(True, location="t")

    def test_invalid_string_rejected(self):
        with pytest.raises(ContractValidationError):
            parse_decimal("12,34", location="t")


class TestMoney:
    def test_creation_and_quantize(self):
        money = Money.from_value("10.256", "USD")
        assert money.quantized().amount == Decimal("10.26")  # HALF_EVEN -> 10.26
        assert money.canonical_string() == "10.26"

    def test_half_even_rounding(self):
        # 10.245 with HALF_EVEN rounds to 10.24 (even last digit)
        assert Money.from_value("10.245", "USD").quantized().amount == Decimal("10.24")
        # 10.255 with HALF_EVEN rounds to 10.26 (even last digit)
        assert Money.from_value("10.255", "USD").quantized().amount == Decimal("10.26")

    def test_jpy_precision_zero(self):
        assert Money.from_value("1000.5", "JPY").quantized().amount == Decimal("1000")

    def test_arithmetic_same_currency(self):
        total = Money.from_value("10.10", "USD") + Money.from_value("5.05", "USD")
        assert total.amount == Decimal("15.15")
        diff = Money.from_value("10.10", "USD") - Money.from_value("0.10", "USD")
        assert diff.amount == Decimal("10.00")

    def test_cross_currency_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            Money.from_value("1", "USD") + Money.from_value("1", "EUR")
        assert excinfo.value.rule_id == "FX-BOUNDARY"

    def test_money_from_float_rejected(self):
        with pytest.raises(ContractValidationError):
            Money.from_value(1.5, "USD")

    def test_money_unknown_currency_rejected(self):
        with pytest.raises(ContractValidationError):
            Money.from_value("1", "ZZZ")

    def test_negation(self):
        assert (-Money.from_value("5", "USD")).amount == Decimal("-5")
