"""Validation and calculation unit tests."""

from __future__ import annotations

from datetime import date

import pytest

from bnadataentry.calculations import (
    compute_line_totals,
    compute_summary,
    effective_retro_pct,
    effective_tax_pct,
)
from bnadataentry.clients import get_client
from bnadataentry.models import InvoiceSummary, LineItem
from bnadataentry.validation import (
    is_foc_value,
    parse_date,
    parse_number,
)


def test_parse_date_dmy() -> None:
    assert parse_date("21.7.2026") == date(2026, 7, 21)
    assert parse_date("05.10.2026") == date(2026, 10, 5)
    assert parse_date("") is None
    assert parse_date(None) is None


def test_parse_date_invalid() -> None:
    with pytest.raises(ValueError):
        parse_date("not-a-date")


def test_foc_detection_and_number() -> None:
    assert is_foc_value("FOC")
    assert is_foc_value("foc")
    assert parse_number("FOC", allow_foc=True) == 0.0
    assert parse_number("1,040.5") == 1040.5


def test_foc_line_totals_zero() -> None:
    line = LineItem(
        product_description="Sample FOC",
        pack_qty=50,
        list_price=100,
        is_foc=True,
    )
    compute_line_totals(line)
    assert line.line_total == 0.0
    assert line.grand_total == 0.0


def test_capital_default_retro_3_percent() -> None:
    lines = [
        LineItem(product_description="YMK", pack_qty=200, list_price=520),
    ]
    summary = compute_summary("capital", lines)
    assert summary.tax_amount == 0.0
    assert summary.retro_amount == pytest.approx(-3120.0)
    assert summary.delivery_charge == pytest.approx(0.0)
    assert summary.balance_to_receive == pytest.approx(100880.0)


def test_capital_dc_not_applied_on_invoice_balance() -> None:
    """Capital DC is deferred to payment — invoice balance is after retro only."""
    from bnadataentry.calculations import (
        capital_dc_charge,
        dc_applies_at_payment,
        suggest_capital_dc,
    )

    lines = [
        LineItem(product_description="YMK", pack_qty=200, list_price=520),
    ]
    summary = compute_summary("capital", lines, branch="CREL")
    merchandise = 104000.0
    retro = -3120.0
    invoice_balance = merchandise + retro
    assert dc_applies_at_payment("capital") is True
    assert summary.delivery_charge == pytest.approx(0.0)
    assert summary.balance_to_receive == pytest.approx(invoice_balance)
    suggested = suggest_capital_dc(invoice_balance, "CREL")
    assert suggested == pytest.approx(capital_dc_charge(invoice_balance, "CREL"))
    assert suggested > 0



def test_balance_formula_subtracts_delivery_charge() -> None:
    from bnadataentry.calculations import balance_to_receive_amount

    assert balance_to_receive_amount(1000, 50, -30, 20) == pytest.approx(1000)
    # merch + tax + retro - dc = 1000 + 50 - 30 - 20 = 1000


def test_makro_tax_5_and_retro_5() -> None:
    lines = [
        LineItem(product_description="Ajino", pack_qty=20, list_price=2760),
        LineItem(product_description="HB", pack_qty=96, list_price=1940),
    ]
    summary = compute_summary("makro", lines)
    merchandise = 55200 + 186240
    assert merchandise == 241440
    assert summary.tax_amount == pytest.approx(12072.0)
    # retro on (merchandise + tax)
    expected_retro = -round((merchandise + 12072.0) * 0.05, 6)
    assert summary.retro_amount == pytest.approx(expected_retro)
    assert summary.balance_to_receive == pytest.approx(
        merchandise + 12072.0 + expected_retro
    )


def test_override_wins_over_default() -> None:
    lines = [LineItem(product_description="X", pack_qty=10, list_price=100)]
    summary = InvoiceSummary(tax_pct_override=0.0, retro_pct_override=10.0)
    result = compute_summary("makro", lines, summary)
    client = get_client("makro")
    assert effective_tax_pct(client, result) == 0.0
    assert effective_retro_pct(client, result) == 10.0
    assert result.tax_amount == 0.0
    assert result.retro_amount == pytest.approx(-100.0)
    assert result.balance_to_receive == pytest.approx(900.0)


def test_stored_amounts_preserved_when_flag_set() -> None:
    lines = [LineItem(product_description="X", pack_qty=10, list_price=100)]
    summary = InvoiceSummary(
        tax_amount=50.0,
        retro_amount=-25.0,
        balance_to_receive=1025.0,
    )
    result = compute_summary("capital", lines, summary, use_stored_amounts=True)
    assert result.tax_amount == 50.0
    assert result.retro_amount == -25.0
    assert result.balance_to_receive == 1025.0
