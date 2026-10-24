"""Direct tests for functions the integration suite only touches indirectly."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from bnadataentry.calculations import (
    capital_dc_charge,
    dc_applies_at_payment,
    refresh_invoice,
    sum_line_amounts,
)
from bnadataentry.clients import all_clients, get_client
from bnadataentry.db import init_db, list_tables
from bnadataentry.excel_export import _fmt_date, _sheet_name_for_branch
from bnadataentry.excel_import import (
    _branch_from_sheet,
    _fmt_summary_date,
    _invoice_key,
    _normalize_header,
    _num_or_zero,
    _safe_upload_filename,
    detect_client_from_filename,
    find_client_workbook,
)
from bnadataentry.expected_payment import (
    PaymentTerms,
    expected_payment,
    money,
    strategy_for,
)
from bnadataentry.models import Invoice, InvoiceSummary, LineItem
from bnadataentry.payments import ar_snapshot, utc_today
from bnadataentry.reconciliation import (
    ReconciliationRepository,
    SupermarketInvoice,
    SupermarketPayment,
    capital_delivery_rate,
    default_terms,
    shops_for,
)
from bnadataentry.repository import InvoiceRepository
from bnadataentry.ui_helpers import (
    _as_date,
    _short_product_label,
    browse_metric_specs,
)
from bnadataentry.validation import (
    is_total_row,
    normalize_invoice_no,
    parse_date,
    parse_number,
    require_non_empty,
)


def test_normalize_invoice_no_strips_float_suffix() -> None:
    assert normalize_invoice_no(1028.0) == "1028"
    assert normalize_invoice_no("  0121  ") == "0121"
    assert normalize_invoice_no(None) == ""


def test_require_non_empty_rejects_blank() -> None:
    assert require_non_empty("  A1 ", "Invoice No") == "A1"
    with pytest.raises(ValueError, match="Invoice No is required"):
        require_non_empty("  ", "Invoice No")


def test_parse_date_accepts_datetime_and_iso() -> None:
    assert parse_date(datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)) == date(
        2026, 7, 21
    )
    assert parse_date("2026-07-21") == date(2026, 7, 21)
    assert parse_date("   ") is None


def test_parse_number_commas_nan_and_invalid() -> None:
    assert parse_number("1,040.5") == 1040.5
    assert parse_number(float("nan")) == 0.0
    assert parse_number(None) == 0.0
    with pytest.raises(ValueError, match="Unrecognized number"):
        parse_number("abc")


def test_is_total_row_labels() -> None:
    assert is_total_row("Grand Total")
    assert is_total_row("sub-total")
    assert is_total_row("Total Amount")
    assert not is_total_row("Yomae Krua Sardine")
    assert not is_total_row(None)


def test_all_clients_and_unknown_id() -> None:
    ids = [client.id for client in all_clients()]
    assert ids == ["capital", "makro", "one_stop_mart"]
    with pytest.raises(KeyError, match="Unknown client_id"):
        get_client("unknown")


def test_sum_line_amounts_and_refresh_invoice() -> None:
    lines = [
        LineItem(product_description="A", pack_qty=2, list_price=10),
        LineItem(product_description="B", pack_qty=1, list_price=5, is_foc=True),
    ]
    assert sum_line_amounts(lines) == pytest.approx(20.0)
    invoice = refresh_invoice(
        Invoice(
            client_id="makro",
            invoice_no="1",
            branch="Sat San",
            lines=lines,
            summary=InvoiceSummary(),
        )
    )
    assert invoice.summary.tax_amount == pytest.approx(1.0)
    assert invoice.summary.balance_to_receive == pytest.approx(19.95)


def test_capital_dc_branch_rates() -> None:
    assert dc_applies_at_payment("makro") is False
    assert capital_dc_charge(1050, "Hyper") == 0.0
    assert capital_dc_charge(0, "CREL") == 0.0
    assert capital_dc_charge(1050, "DC MDY") == pytest.approx(60.0)
    assert capital_dc_charge(-1050, "CREL") == pytest.approx(40.0)


def test_utc_today_and_non_capital_snapshot() -> None:
    assert isinstance(utc_today(), date)
    invoice = Invoice(
        client_id="makro",
        invoice_no="9",
        due_date=date(2020, 1, 1),
        summary=InvoiceSummary(
            balance_to_receive=1000,
            delivery_charge=50,
        ),
    )
    snap = ar_snapshot(invoice, amount_paid=200, today=date(2026, 1, 1))
    assert snap["amount_due"] == pytest.approx(1000)
    assert snap["delivery_charge"] == 0.0
    assert snap["outstanding"] == pytest.approx(800)
    assert snap["is_overdue"] is True
    assert snap["dc_at_payment"] is False


def test_invoice_filter_dict() -> None:
    invoice = Invoice(
        client_id="capital",
        invoice_no="10",
        invoice_date=date(2026, 1, 2),
        branch="Hyper",
    )
    assert invoice.to_filter_dict() == {
        "client_id": "capital",
        "invoice_no": "10",
        "invoice_date": date(2026, 1, 2),
        "branch": "Hyper",
    }


def test_money_and_unknown_strategy() -> None:
    assert money(10) == 10.0
    assert money(10.126) == 10.13
    with pytest.raises(ValueError, match="Unknown supermarket"):
        strategy_for("Unknown Market")
    result = expected_payment(
        "Capital",
        PaymentTerms(total_invoice_amount=1000, is_tax_included=False),
    )
    assert result.steps[-1][0] == "Expected final payment"


def test_default_terms_follow_supermarket() -> None:
    capital = SupermarketInvoice(
        invoice_number="C",
        supermarket_name="Capital",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_invoice_amount=100,
        is_tax_included=True,
    )
    makro = SupermarketInvoice(
        invoice_number="M",
        supermarket_name="Makro",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_invoice_amount=100,
    )
    osm = SupermarketInvoice(
        invoice_number="O",
        supermarket_name="One Stop Mart",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_invoice_amount=100,
    )
    assert default_terms(capital).retro_percentage == 0.03
    assert default_terms(capital).is_tax_included is True
    assert default_terms(makro).retro_percentage == 0.0
    assert default_terms(osm).retro_percentage == 0.0
    hyper = SupermarketInvoice(
        invoice_number="H",
        supermarket_name="Capital",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_invoice_amount=100,
        shop="Hyper",
    )
    mdy = SupermarketInvoice(
        invoice_number="D",
        supermarket_name="Capital",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_invoice_amount=100,
        shop="DC MDY",
    )
    assert shops_for("Capital") == (
        "CREL Distribution Center",
        "DC MDY",
        "G&G",
        "Hyper",
    )
    assert shops_for("Makro") == ("Makro-Sat Sun", "Makro - Kha Wei Chan")
    assert capital_delivery_rate("Hyper") == 0.0
    assert capital_delivery_rate("DC MDY") == 0.06
    assert capital_delivery_rate("CREL") == 0.04
    assert default_terms(hyper).delivery_charge_percentage == 0.0
    assert default_terms(mdy).delivery_charge_percentage == 0.0


def test_reconciliation_validation_and_delete(tmp_path: Path) -> None:
    repo = ReconciliationRepository(init_db(tmp_path / "recon.db"))
    with pytest.raises(ValueError, match="Invoice number is required"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="  ",
                supermarket_name="Makro",
                shop="Makro-Sat Sun",
                invoice_date=date(2026, 1, 1),
                due_date=date(2026, 2, 1),
                total_invoice_amount=10,
            )
        )
    with pytest.raises(ValueError, match="greater than zero"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="Z",
                supermarket_name="Makro",
                shop="Makro-Sat Sun",
                invoice_date=date(2026, 1, 1),
                due_date=date(2026, 2, 1),
                total_invoice_amount=0,
            )
        )
    with pytest.raises(ValueError, match="Due date"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="Z",
                supermarket_name="Makro",
                shop="Makro-Sat Sun",
                invoice_date=date(2026, 2, 1),
                due_date=date(2026, 1, 1),
                total_invoice_amount=10,
            )
        )
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="OSM-1",
            supermarket_name="One Stop Mart",
            shop="One Stop Mart",
            invoice_date=date(2026, 3, 1),
            due_date=date(2026, 4, 1),
            total_invoice_amount=1000,
            is_tax_included=True,
        )
    )
    assert saved.is_tax_included is False
    assert saved.id is not None
    with pytest.raises(ValueError, match="cannot be negative"):
        repo.create_payment(
            SupermarketPayment(
                invoice_id=saved.id,
                payment_date=date(2026, 3, 2),
                actual_received_amount=-1,
            )
        )
    with pytest.raises(ValueError, match="Retro"):
        repo.create_payment(
            SupermarketPayment(
                invoice_id=saved.id,
                payment_date=date(2026, 3, 2),
                actual_received_amount=100,
                retro_percentage=1.5,
            )
        )
    with pytest.raises(ValueError, match="Invoice not found"):
        repo.create_payment(
            SupermarketPayment(
                invoice_id=9999,
                payment_date=date(2026, 3, 2),
                actual_received_amount=10,
            )
        )
    payment = repo.create_payment(
        SupermarketPayment(
            invoice_id=saved.id,
            payment_date=date(2026, 3, 2),
            actual_received_amount=900,
            retro_percentage=0.05,
            delivery_charge_percentage=0.05,
        )
    )
    assert payment.id is not None
    row = repo.report_rows("One Stop Mart")[0]
    assert row["Expected Final Payment"] == 900.0
    assert row["Status"] == "Paid"
    repo.delete_payment(payment.id)
    assert repo.list_payments(saved.id) == []
    assert repo.get_invoice(saved.id).status == "Pending"  # type: ignore[union-attr]
    repo.delete_invoice(saved.id)
    assert repo.get_invoice(saved.id) is None
    assert repo.report_rows() == []


def test_supermarket_tables_are_created(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "schema.db")
    tables = list_tables(conn)
    assert "supermarket_invoices" in tables
    assert "supermarket_payments" in tables
    conn.close()


def test_repository_lookup_and_counts(tmp_path: Path) -> None:
    repo = InvoiceRepository(init_db(tmp_path / "repo.db"))
    assert repo.get_invoice(1) is None
    assert repo.find_by_key("capital", "missing", date(2026, 1, 1)) is None
    created = repo.create_invoice(
        Invoice(
            client_id="capital",
            invoice_no="77",
            invoice_date=date(2026, 5, 1),
            branch="Hyper",
            lines=[LineItem(product_description="Item", pack_qty=1, list_price=10)],
            summary=InvoiceSummary(),
        )
    )
    found = repo.find_by_key(
        "capital", "77", date(2026, 5, 1), branch="Hyper"
    )
    assert found is not None
    assert found.id == created.id
    assert repo.count_invoices("capital") == 1
    assert repo.count_line_items("capital") == 1
    assert repo.count_invoices("makro") == 0


def test_excel_filename_and_header_helpers(tmp_path: Path) -> None:
    assert detect_client_from_filename(Path("Capital 2026.xlsx")) == "capital"
    assert detect_client_from_filename(Path("Makro.xlsx")) == "makro"
    assert detect_client_from_filename(Path("One Stop Mart.xlsx")) == "one_stop_mart"
    assert detect_client_from_filename(Path("notes.xlsx")) is None
    assert _safe_upload_filename("../Secret.xlsx") == "Secret.xlsx"
    assert _safe_upload_filename("a:b.xlsx") == "a_b.xlsx"
    assert _num_or_zero("1,250") == 1250
    assert _num_or_zero("nope") == 0.0
    assert _fmt_summary_date("21.7.2026") == "2026-07-21"
    assert _fmt_summary_date("") == ""
    assert _fmt_date(None) == ""
    assert _fmt_date(date(2026, 7, 21)) == "21.7.2026"
    assert _sheet_name_for_branch("capital", "CREL") == "CREL Distribution Center"
    assert _sheet_name_for_branch("makro", "Other") == "Other"
    client = get_client("capital")
    assert _branch_from_sheet(client, "CREL Distribution Center") == "CREL"
    assert _normalize_header("PO Date") == "po_date"
    invoice = Invoice(
        client_id="capital",
        invoice_no="1",
        invoice_date=date(2026, 1, 1),
        branch="CREL",
    )
    assert _invoice_key(invoice) == ("capital", "1", "2026-01-01", "CREL")
    assert find_client_workbook("capital", tmp_path) is None


def test_ui_label_and_date_helpers() -> None:
    assert _short_product_label([]) == ""
    assert _short_product_label(
        [LineItem(product_description="Sardine")]
    ) == "Sardine"
    label = _short_product_label(
        [
            LineItem(product_description="Sardine"),
            LineItem(product_description="Milk"),
        ]
    )
    assert label == "Sardine +1 more"
    assert _as_date(date(2026, 1, 1), "Invoice Date") == date(2026, 1, 1)
    assert _as_date("", "PO Date", required=False) is None
    with pytest.raises(ValueError, match="Invoice Date is required"):
        _as_date(None, "Invoice Date")
    specs = browse_metric_specs(
        "capital",
        {
            "merchandise_total": 100,
            "discount_total": 5,
            "grand_total": 95,
            "retro": -3,
            "delivery_charge": 4,
            "balance_to_receive": 88,
        },
    )
    assert specs[0] == ("Total Amount", 100)
    assert specs[-1][0] == "Balance"
