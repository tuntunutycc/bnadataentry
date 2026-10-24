"""Makro Pack Qty ဖာ / Pcs alignment tests."""

from __future__ import annotations

from datetime import date

import pytest

from bnadataentry.ui_helpers import (
    build_invoice_from_form,
    detail_sheet_columns,
)


def test_makro_detail_columns_include_fa_and_pcs() -> None:
    cols = detail_sheet_columns("makro")
    assert cols.index("pack_qty_fa") < cols.index("pack_qty")
    assert "pack_qty_fa" in cols
    assert "pack_qty" in cols


def test_makro_form_stores_fa_and_pcs() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "makro",
            "branch": "Sat San",
            "invoice_no": "0335",
            "invoice_date": date(2026, 3, 4),
            "tax_pct_override": 0,
            "discount_pct": 0,
            "retro_pct_override": 5,
            "lines": [
                {
                    "product_description": "Yobick Org 80 ml",
                    "pack_qty_fa": 0,
                    "pack_qty": 120,
                    "list_price": 1500,
                }
            ],
        }
    )
    assert invoice.lines[0].pack_qty_fa == 0.0
    assert invoice.lines[0].pack_qty == 120.0
    assert invoice.lines[0].line_total == pytest.approx(180000.0)
