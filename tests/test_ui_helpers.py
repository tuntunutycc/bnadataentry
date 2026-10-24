"""UI helper tests for branch-sheet form → invoice conversion."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bnadataentry.db import init_db
from bnadataentry.models import Invoice, InvoiceSummary, LineItem
from bnadataentry.repository import InvoiceRepository
from bnadataentry.ui_helpers import (
    apply_line_totals,
    build_invoice_from_form,
    detail_sheet_columns,
    invoice_to_browse_row,
    preview_invoice_totals,
)


def test_capital_detail_columns_match_sheet() -> None:
    cols = detail_sheet_columns("capital")
    assert cols == [
        "product_description",
        "pack_qty",
        "pack_qty_fa",
        "list_price",
        "line_total",
    ]
    assert cols.index("pack_qty") < cols.index("pack_qty_fa")  # Pk then Unit
    assert "tax_amount" not in cols
    assert "discount_amount" not in cols
    assert "is_foc" not in cols


def test_client_templates_differ() -> None:
    from bnadataentry.ui_helpers import (
        client_template_blurb,
        pack_qty_editor_labels,
    )

    assert "Pk | Unit" in client_template_blurb("capital")
    assert "ဖာ | Pcs" in client_template_blurb("makro")
    assert "Total Invoice" in client_template_blurb("makro")
    assert "Tax 5%" in client_template_blurb("makro")
    assert "no PO No" in client_template_blurb("makro")

    assert pack_qty_editor_labels("capital")["pack_qty"] == "Pack Qty (Pk)"
    assert pack_qty_editor_labels("makro")["pack_qty"] == "Pack Qty (Pcs)"
    assert pack_qty_editor_labels("makro")["pack_qty_fa"] == "Pack Qty (ဖာ)"
    assert pack_qty_editor_labels("one_stop_mart")["pack_qty"] == "Pack Qty (Pk (pcs))"
    assert "pack_qty_fa" not in pack_qty_editor_labels("one_stop_mart")

    osm_cols = detail_sheet_columns("one_stop_mart")
    assert osm_cols == [
        "product_description",
        "pack_qty",
        "list_price",
        "line_total",
    ]
    assert "pack_qty_fa" not in osm_cols
    assert "is_foc" not in osm_cols

    makro_cols = detail_sheet_columns("makro")
    assert makro_cols == [
        "product_description",
        "pack_qty_fa",
        "pack_qty",
        "list_price",
        "line_total",
        "tax_amount",
        "grand_total",
        "is_foc",
    ]


def test_capital_editor_includes_unit_subcolumn() -> None:
    from bnadataentry.ui_helpers import empty_detail_line, invoice_lines_for_editor

    empty = empty_detail_line("capital")
    assert "pack_qty" in empty and "pack_qty_fa" in empty
    assert empty["pack_qty_fa"] == 0.0

    inv = Invoice(
        client_id="capital",
        invoice_no="1",
        invoice_date=date(2026, 1, 1),
        branch="CREL",
        lines=[
            LineItem(
                product_description="x",
                pack_qty=10,
                pack_qty_fa=2,
                list_price=100,
                discount_amount=0,
            )
        ],
    )
    row = invoice_lines_for_editor(inv, "capital")[0]
    assert row["pack_qty"] == 10
    assert row["pack_qty_fa"] == 2


def test_build_capital_invoice_persists_unit() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "CREL",
            "invoice_no": "9999",
            "invoice_date": date(2026, 1, 1),
            "po_no": "CPO-1",
            "po_date": date(2026, 1, 1),
            "due_date": date(2026, 2, 1),
            "discount_pct": 3.0,
            "lines": [
                {
                    "product_description": "Test",
                    "pack_qty": 100,
                    "pack_qty_fa": 5,
                    "list_price": 10,
                    "discount_amount": 0,
                }
            ],
        }
    )
    assert invoice.lines[0].pack_qty == 100
    assert invoice.lines[0].pack_qty_fa == 5  # Unit
    assert invoice.lines[0].line_total == pytest.approx(1000.0)


def test_apply_line_totals_auto_discount() -> None:
    rows = apply_line_totals(
        [
            {
                "product_description": "Yomae Krua Sardine",
                "pack_qty": 200,
                "list_price": 520,
                "discount_amount": 0,
            }
        ],
        client_id="capital",
        discount_pct=3.0,
    )
    assert len(rows) == 1
    assert rows[0]["line_total"] == pytest.approx(104000.0)
    assert rows[0]["discount_amount"] == pytest.approx(3120.0)
    assert rows[0]["grand_total"] == pytest.approx(100880.0)


def test_filter_drops_excel_total_footer_rows() -> None:
    from bnadataentry.ui_helpers import (
        filter_product_line_rows,
        line_totals_footer,
    )

    kept = filter_product_line_rows(
        [
            {"product_description": "Jelly Grape", "pack_qty": 110, "list_price": 1140},
            {"product_description": "Total", "pack_qty": 0, "list_price": 0},
            {"product_description": "Grand Total", "pack_qty": 0, "list_price": 0},
            {"product_description": "Jelly Peach", "pack_qty": 100, "list_price": 1140},
            {"product_description": "", "pack_qty": 1, "list_price": 1},
        ]
    )
    assert [r["product_description"] for r in kept] == ["Jelly Grape", "Jelly Peach"]

    # Also drop when PO / invoice label is Total (merged footer cell)
    kept2 = filter_product_line_rows(
        [{"product_description": "x", "po_no": "Total", "pack_qty": 1, "list_price": 1}]
    )
    assert kept2 == []

    prepared = apply_line_totals(
        [
            {
                "product_description": "A",
                "pack_qty": 110,
                "list_price": 1140,
                "discount_amount": 3762,
            },
            {
                "product_description": "Total",
                "pack_qty": 0,
                "list_price": 0,
                "discount_amount": 17100,
            },
            {
                "product_description": "B",
                "pack_qty": 100,
                "list_price": 1140,
                "discount_amount": 3420,
            },
        ],
        client_id="capital",
        discount_pct=3.0,
    )
    assert len(prepared) == 2
    footer = line_totals_footer(prepared)
    assert footer["merchandise_total"] == pytest.approx(125400 + 114000)
    assert footer["discount_total"] == pytest.approx(3762 + 3420)
    assert footer["grand_total"] == pytest.approx(121638 + 110580)


def test_save_ignores_total_row_in_payload() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "CREL",
            "invoice_no": "2124",
            "invoice_date": date(2026, 7, 4),
            "po_no": "CPO-2627105",
            "discount_pct": 3.0,
            "lines": [
                {
                    "product_description": "SunSu Chewy Grape",
                    "pack_qty": 110,
                    "list_price": 1140,
                    "discount_amount": 3762,
                },
                {
                    "product_description": "Total",
                    "pack_qty": 0,
                    "list_price": 0,
                    "discount_amount": 17100,
                    "line_total": 570000,
                },
            ],
        }
    )
    assert len(invoice.lines) == 1
    assert invoice.lines[0].product_description == "SunSu Chewy Grape"
    merch = 110 * 1140
    retro = -3762.0
    # Capital: DC deferred to payment — invoice balance is after retro only.
    assert invoice.summary.delivery_charge == pytest.approx(0.0)
    assert invoice.summary.balance_to_receive == pytest.approx(merch + retro)


def test_build_capital_invoice_like_crel_sheet() -> None:
    """Mirrors CREL invoice 1028-style entry (header + one line)."""
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "CREL",
            "invoice_no": "1028",
            "invoice_date": date(2026, 7, 21),
            "po_no": "CPO-2633368",
            "po_date": date(2026, 7, 21),
            "due_date": date(2026, 8, 21),
            "discount_pct": 3.0,
            "remark": "Delivery charges note",
            "lines": [
                {
                    "product_description": "Yomae Krua Sardine in Tomato Sauce155g",
                    "pack_qty": 200,
                    "list_price": 520,
                    "discount_amount": 0,
                }
            ],
        }
    )
    assert invoice.branch == "CREL"
    assert invoice.po_no == "CPO-2633368"
    assert len(invoice.lines) == 1
    assert invoice.lines[0].line_total == pytest.approx(104000.0)
    assert invoice.lines[0].discount_amount == pytest.approx(3120.0)
    assert invoice.lines[0].grand_total == pytest.approx(100880.0)
    assert invoice.summary.branch_amounts["CREL"] == pytest.approx(104000.0)
    assert invoice.summary.retro_amount == pytest.approx(-3120.0)
    assert invoice.summary.delivery_charge == pytest.approx(0.0)
    assert invoice.summary.balance_to_receive == pytest.approx(104000 - 3120)
    assert invoice.summary.tax_amount == 0.0


def test_build_multi_line_capital_invoice() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "G&G",
            "invoice_no": "1029",
            "invoice_date": date(2026, 7, 21),
            "po_date": date(2026, 7, 16),
            "due_date": date(2026, 8, 21),
            "discount_pct": 4.0,
            "lines": [
                {
                    "product_description": "Yobick Original Yoghurt 80 ml",
                    "pack_qty": 480,
                    "list_price": 392,
                },
                {
                    "product_description": "Yobick Strawberry Yoghurt 80 ml",
                    "pack_qty": 360,
                    "list_price": 392,
                },
                {
                    "product_description": "Able Farm Low Fat 200 ml",
                    "pack_qty": 120,
                    "list_price": 720,
                },
            ],
        }
    )
    assert len(invoice.lines) == 3
    assert invoice.summary.branch_amounts["G&G"] == pytest.approx(415680.0)
    assert sum(line.discount_amount for line in invoice.lines) == pytest.approx(16627.2)
    merch = 415680.0
    retro = -16627.2
    assert invoice.summary.delivery_charge == pytest.approx(0.0)
    assert invoice.summary.balance_to_receive == pytest.approx(merch + retro)


def test_explicit_discount_wins_over_pct() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "Hyper",
            "invoice_no": "1159",
            "invoice_date": date(2026, 8, 14),
            "discount_pct": 3.0,
            "lines": [
                {
                    "product_description": "Sunsu Konjac Snack Org Mala",
                    "pack_qty": 200,
                    "list_price": 1008,
                    "discount_amount": 6048,
                }
            ],
        }
    )
    assert invoice.lines[0].discount_amount == pytest.approx(6048.0)
    assert invoice.lines[0].grand_total == pytest.approx(195552.0)


def test_save_and_reload_via_repository(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "edit.db")
    repo = InvoiceRepository(conn)
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "DC MDY",
            "invoice_no": "0669",
            "invoice_date": date(2026, 9, 5),
            "po_no": "CPO-2657627",
            "po_date": date(2026, 9, 2),
            "due_date": date(2026, 10, 5),
            "discount_pct": 3.0,
            "lines": [
                {
                    "product_description": "Sunsu Konjac Snack - Original Mala",
                    "pack_qty": 60,
                    "list_price": 1008,
                },
                {
                    "product_description": "Sunsu Konjac Snack - Extra Mala",
                    "pack_qty": 60,
                    "list_price": 1008,
                },
            ],
        }
    )
    created = repo.create_invoice(invoice, recompute=False)
    loaded = repo.get_invoice(created.id)  # type: ignore[arg-type]
    assert loaded is not None
    assert loaded.branch == "DC MDY"
    assert loaded.invoice_no == "0669"
    assert len(loaded.lines) == 2
    merch = 60 * 1008 * 2
    retro = -round(merch * 0.03, 6)
    assert loaded.summary.delivery_charge == pytest.approx(0.0)
    assert loaded.summary.balance_to_receive == pytest.approx(merch + retro)

    # Edit: change qty on first line
    loaded.lines[0].pack_qty = 80
    updated_payload = {
        "client_id": "capital",
        "branch": loaded.branch,
        "invoice_no": loaded.invoice_no,
        "invoice_date": loaded.invoice_date,
        "po_no": loaded.po_no,
        "po_date": loaded.po_date,
        "due_date": loaded.due_date,
        "discount_pct": 3.0,
        "lines": [
            {
                "product_description": line.product_description,
                "pack_qty": line.pack_qty,
                "list_price": line.list_price,
                "discount_amount": 0,
            }
            for line in loaded.lines
        ],
    }
    edited = build_invoice_from_form(updated_payload)
    edited.id = loaded.id
    repo.update_invoice(edited, recompute=False)
    again = repo.get_invoice(loaded.id)  # type: ignore[arg-type]
    assert again is not None
    assert again.lines[0].pack_qty == 80
    assert again.lines[0].line_total == pytest.approx(80640.0)


def test_makro_still_supports_tax_and_foc() -> None:
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
                    "pack_qty": 120,
                    "list_price": 1500,
                }
            ],
        }
    )
    assert invoice.lines[0].line_total == pytest.approx(180000.0)
    assert invoice.summary.tax_amount == 0.0
    assert invoice.summary.retro_amount == pytest.approx(-9000.0)


def test_manual_summary_override_1c() -> None:
    invoice = build_invoice_from_form(
        {
            "client_id": "capital",
            "branch": "CREL",
            "invoice_no": "9999",
            "invoice_date": date(2026, 7, 1),
            "product_label": "Manual Test",
            "retro_pct_override": 3,
            "discount_pct": 3,
            "use_manual_summary": True,
            "tax_amount": 0,
            "retro_amount": -5000,
            "balance_to_receive": 99000,
            "lines": [
                {
                    "product_description": "Sample",
                    "pack_qty": 100,
                    "list_price": 1000,
                }
            ],
        }
    )
    assert invoice.summary.retro_amount == -5000
    assert invoice.summary.balance_to_receive == 99000
    assert invoice.product_label == "Manual Test"
    # Lines still stored
    assert invoice.lines[0].line_total == pytest.approx(100000.0)


def test_browse_row() -> None:
    from bnadataentry.ui_helpers import CAPITAL_BROWSE_COLUMNS

    inv = Invoice(
        id=7,
        client_id="capital",
        invoice_no="1028",
        invoice_date=date(2026, 7, 21),
        branch="CREL",
        po_no="CPO-1",
        product_label="YMK",
        lines=[
            LineItem(
                product_description="x",
                pack_qty=200,
                pack_qty_fa=0,
                list_price=520,
                line_total=104000,
                discount_amount=3120,
                grand_total=100880,
            )
        ],
        summary=InvoiceSummary(tax_amount=0, retro_amount=-3120, balance_to_receive=100880),
    )
    totals = preview_invoice_totals(inv)
    assert totals["grand_total"] == pytest.approx(100880.0)
    row = invoice_to_browse_row(inv)
    assert row["Branch"] == "CREL"
    assert row["PO No"] == "CPO-1"
    assert row["Invoice No"] == "1028"
    assert row["Pack Qty (Pk)"] == 200
    assert row["Pack Qty (Unit)"] == ""  # blank Unit in Excel
    assert row["Discount 5%, 3%"] == pytest.approx(3120.0)
    assert [k for k in row if k != "_id"] == list(CAPITAL_BROWSE_COLUMNS)
    assert "Tax" not in row
    assert "Balance" not in row
    assert "Retro (3%)" not in row
    assert "Remark" not in row


def test_browse_universal_template_pack_qty_dual() -> None:
    from bnadataentry.ui_helpers import (
        CAPITAL_BROWSE_COLUMNS,
        browse_table_rows,
    )

    capital = Invoice(
        id=2,
        client_id="capital",
        invoice_no="1028",
        invoice_date=date(2026, 7, 21),
        branch="CREL",
        po_no="PO1",
        lines=[
            LineItem(
                product_description="x",
                pack_qty=200,
                pack_qty_fa=0,
                list_price=520,
                line_total=104000,
                discount_amount=3120,
                grand_total=100880,
            )
        ],
    )
    gng = Invoice(
        id=3,
        client_id="capital",
        invoice_no="2119",
        invoice_date=date(2026, 7, 2),
        branch="G&G",
        lines=[
            LineItem(
                product_description="Yobick",
                pack_qty=600,
                list_price=392,
                line_total=235200,
                discount_amount=9408,
                grand_total=225792,
            )
        ],
    )

    rows = browse_table_rows([capital, gng], "capital")
    # 2 product rows + 2 Total footers
    assert len(rows) == 4
    product_rows = [r for r in rows if r["Invoice No"] != "Total"]
    total_rows = [r for r in rows if r["Invoice No"] == "Total"]
    assert len(product_rows) == 2 and len(total_rows) == 2
    assert {r["Branch"] for r in product_rows} == {"CREL", "G&G"}
    for r in rows:
        assert [k for k in r if k != "_id"] == list(CAPITAL_BROWSE_COLUMNS)
        assert "Pack Qty (Pk)" in r and "Pack Qty (Unit)" in r
        assert "Discount 5%, 3%" in r
        assert "Retro (3%)" not in r
        assert "Balance (To Receive)" not in r

    c = product_rows[0]
    assert c["Invoice No"] == "1028"
    assert c["Pack Qty (Pk)"] == 200
    assert c["PO No"] == "PO1"
    assert next(iter(c.keys())) == "Invoice No"


def test_multiline_invoice_excel_style_blank_headers() -> None:
    """Continuation rows leave PO/dates/Invoice No blank; Total footer like Excel."""
    from bnadataentry.ui_helpers import CAPITAL_BROWSE_COLUMNS, browse_table_rows

    inv = Invoice(
        id=10,
        client_id="capital",
        invoice_no="1018",
        invoice_date=date(2026, 7, 16),
        po_date=date(2026, 7, 15),
        due_date=date(2026, 8, 16),
        branch="CREL",
        po_no="CPO-2633440",
        lines=[
            LineItem(
                product_description="SunSu Chewy Grape",
                pack_qty=40,
                list_price=1140,
                line_total=45600,
                discount_amount=1368,
                grand_total=44232,
            ),
            LineItem(
                product_description="SunSu Chewy Peach",
                pack_qty=40,
                list_price=1140,
                line_total=45600,
                discount_amount=1368,
                grand_total=44232,
            ),
            LineItem(
                product_description="Sunsu Konjac Org Mala",
                pack_qty=120,
                list_price=1008,
                line_total=120960,
                discount_amount=3629,
                grand_total=117331,
            ),
        ],
    )
    rows = browse_table_rows([inv], "capital")
    assert len(rows) == 4  # 3 products + Total
    assert list(rows[0].keys())[:-1] == list(CAPITAL_BROWSE_COLUMNS)
    # Invoice No is first column
    assert next(iter(rows[0].keys())) == "Invoice No"
    # First row: full header
    assert rows[0]["Invoice No"] == "1018"
    assert rows[0]["PO No"] == "CPO-2633440"
    assert rows[0]["Product Description"] == "SunSu Chewy Grape"
    assert rows[0]["Pack Qty (Pk)"] == 40
    # Continuation: header blank
    assert rows[1]["Invoice No"] == ""
    assert rows[1]["PO No"] == ""
    assert rows[1]["Product Description"] == "SunSu Chewy Peach"
    assert rows[2]["Invoice No"] == ""
    assert rows[2]["Pack Qty (Pk)"] == 120
    # Total footer
    assert rows[3]["Invoice No"] == "Total"
    assert rows[3]["Total"] == pytest.approx(45600 + 45600 + 120960)
    assert rows[3]["Discount 5%, 3%"] == pytest.approx(1368 + 1368 + 3629)
    assert rows[3]["Grand Total"] == pytest.approx(44232 + 44232 + 117331)
    assert rows[0]["Branch"] == rows[1]["Branch"] == "CREL"


def test_browse_columns_differ_by_client() -> None:
    from bnadataentry.ui_helpers import browse_line_rows, summary_sheet_rows

    inv = Invoice(
        id=1,
        client_id="makro",
        invoice_no="0335",
        invoice_date=date(2026, 3, 4),
        branch="Sat San",
        product_label="Yobick",
        lines=[
            LineItem(
                product_description="Yobick",
                pack_qty=120,
                pack_qty_fa=0,
                list_price=1500,
                line_total=180000,
                tax_amount=0,
                grand_total=180000,
            )
        ],
        summary=InvoiceSummary(
            balance_to_receive=171000,
            retro_amount=-9000,
            tax_amount=0,
            remark="Makro detail remark",
            branch_amounts={"Sat San": 180000},
        ),
    )
    makro_lines = browse_line_rows(inv, "makro")[0]
    assert "Pack Qty (ဖာ)" in makro_lines
    assert "Pack Qty (Pcs)" in makro_lines
    assert makro_lines["Pack Qty (Pcs)"] == 120
    assert "Total Invoice" in makro_lines
    assert "Tax 5%" in makro_lines
    assert "Grand Total" in makro_lines
    assert "FOC" not in makro_lines  # FOC is encoded in Total Invoice cell, not a column
    assert "Discount" not in makro_lines

    capital_summary = summary_sheet_rows(
        [
            Invoice(
                id=3,
                client_id="capital",
                invoice_no="1028",
                invoice_date=date(2026, 7, 21),
                po_date=date(2026, 7, 21),
                due_date=date(2026, 8, 21),
                branch="CREL",
                product_label="YMK",
                lines=[
                    LineItem(
                        product_description="x",
                        pack_qty=200,
                        list_price=520,
                        line_total=104000,
                        discount_amount=3120,
                        grand_total=100880,
                    )
                ],
                summary=InvoiceSummary(
                    retro_amount=-3120,
                    delivery_charge=3843.05,
                    # Stored invoice balance is pre-DC; Excel Balance = balance − DC
                    balance_to_receive=100880.0,
                    branch_amounts={"CREL": 104000},
                ),
            )
        ],
        "capital",
    )[0]
    assert capital_summary["CREL"] == pytest.approx(104000)
    assert capital_summary["Retro (3%)"] == pytest.approx(-3120)
    assert "Balance (To Receive)" in capital_summary
    assert "Po Date" in capital_summary
    assert "Subtotal" in capital_summary
    assert "DC Mdy6%, Other 4%, Hyper 0%" in capital_summary
    assert capital_summary["Subtotal"] == pytest.approx(100880)
    assert capital_summary["DC Mdy6%, Other 4%, Hyper 0%"] == pytest.approx(-3843.05)
    assert capital_summary["Balance (To Receive)"] == pytest.approx(97036.95)
    from bnadataentry.ui_helpers import summary_columns

    assert [k for k in capital_summary if k != "_id"] == list(
        summary_columns("capital")
    )

def test_requires_line_items() -> None:
    with pytest.raises(ValueError, match="line item"):
        build_invoice_from_form(
            {
                "client_id": "capital",
                "branch": "CREL",
                "invoice_no": "1",
                "invoice_date": date(2026, 1, 1),
                "lines": [],
            }
        )
