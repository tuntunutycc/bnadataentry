"""Call every library function that the screen tests do not already reach."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from bnadataentry.calculations import suggest_capital_dc
from bnadataentry.db import (
    _add_column,
    _ensure_commercial_columns,
    _ensure_delivery_charge_column,
    _ensure_invoice_category_columns,
    _ensure_makro_rate_columns,
    _ensure_payments_table,
    _ensure_supermarket_shop_column,
    _ensure_tax_treatment_column,
    init_db,
)
from bnadataentry.excel_export import export_client_workbook
from bnadataentry.excel_import import (
    _apply_summary_overlay,
    _fmt_summary_date,
    _normalize_header,
    _summary_overlay_matches,
    import_all_excel_data,
    import_uploaded_workbooks,
    import_workbook,
    load_excel_summary_rows,
)
from bnadataentry.expected_payment import (
    PaymentStrategy,
    PaymentTerms,
    capital_tax_included,
)
from bnadataentry.models import Invoice, InvoiceSummary, LineItem, Payment
from bnadataentry.payments import ar_snapshot
from bnadataentry.reconciliation import (
    InvoiceCategory,
    Product,
    ReconciliationRepository,
    SupermarketInvoice,
    _city_mart_line,
    _date_to_str,
    _normalize_discount,
    _str_to_date,
    discount_note,
    exempt_amount,
)
from bnadataentry.repository import InvoiceRepository
from bnadataentry.ui_helpers import (
    apply_line_totals,
    browse_line_rows,
    browse_metric_specs,
    browse_table_rows,
    client_template_blurb,
    empty_detail_line,
    invoice_lines_for_editor,
    line_totals_footer,
    pack_qty_editor_labels,
    preview_invoice_totals,
    summary_columns,
    summary_sheet_rows,
)
from bnadataentry.validation import is_foc_value, parse_date, parse_number


def _office_invoice(client_id: str, branch: str, number: str) -> Invoice:
    return Invoice(
        client_id=client_id,
        invoice_no=number,
        invoice_date=date(2026, 3, 2),
        po_no="PO-9",
        po_date=date(2026, 3, 1),
        due_date=date(2026, 4, 1),
        branch=branch,
        lines=[
            LineItem(
                product_description="Sardine",
                pack_qty=2,
                list_price=10,
                line_total=20,
                grand_total=20,
            ),
            LineItem(
                product_description="Milk",
                pack_qty=1,
                pack_qty_fa=1,
                list_price=0,
                line_total=0,
                grand_total=0,
                is_foc=True,
            ),
        ],
        summary=InvoiceSummary(
            branch_amounts={branch: 20},
            tax_amount=1,
            retro_amount=-0.6,
            delivery_charge=1,
            balance_to_receive=19.4,
            remark="note",
        ),
    )


def _capital_invoice(**overrides: object) -> SupermarketInvoice:
    values: dict[str, object] = {
        "invoice_number": "C-1",
        "supermarket_name": "Capital",
        "shop": "CREL Distribution Center",
        "invoice_date": date(2026, 1, 1),
        "due_date": date(2026, 1, 31),
        "total_invoice_amount": 1000,
    }
    values.update(overrides)
    return SupermarketInvoice(**values)  # type: ignore[arg-type]


def test_validation_edges() -> None:
    assert parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert parse_number(1.5) == 1.5
    assert parse_number("") == 0.0
    assert parse_number("nan") == 0.0
    assert parse_number("FOC", allow_foc=True) == 0.0
    assert parse_number("   ") == 0.0
    assert is_foc_value(None) is False

    class NAType:
        pass

    assert parse_number(NAType()) == 0.0

    class Unequal:
        def __eq__(self, other: object) -> bool:
            return False

        def __ne__(self, other: object) -> bool:
            raise TypeError("no compare")

    with pytest.raises(ValueError, match="Unrecognized number"):
        parse_number(Unequal())


def test_capital_tax_flag_and_payment_sum() -> None:
    assert capital_tax_included(PaymentTerms(total_invoice_amount=1)) is True
    assert (
        PaymentStrategy.calculate(
            PaymentStrategy, PaymentTerms(total_invoice_amount=1)  # type: ignore[arg-type]
        )
        is None
    )
    assert suggest_capital_dc(1050, "Hyper") == 0.0
    invoice = Invoice(
        client_id="capital",
        invoice_no="1",
        due_date=date(2026, 1, 1),
        summary=InvoiceSummary(balance_to_receive=100, delivery_charge=4),
    )
    snap = ar_snapshot(
        invoice,
        payments=[Payment(invoice_id=1, payment_date=date(2026, 1, 2), amount=10)],
        today=date(2026, 2, 1),
    )
    assert snap["amount_paid"] == 10
    assert snap["amount_due"] == pytest.approx(96)


def test_date_helpers_and_discount_rules() -> None:
    assert _date_to_str(None) is None
    assert _str_to_date(None) is None
    assert _str_to_date("") is None
    assert _str_to_date("2026-01-02") == date(2026, 1, 2)
    assert discount_note(1000, 0) == ""
    assert exempt_amount(_capital_invoice()) == 0
    other = _capital_invoice(supermarket_name="Makro", shop="Makro-Sat Sun")
    other.discount_percentage = 0.03
    _normalize_discount(other)
    assert other.discount_percentage == 0.03
    assert other.remark == "Discount 3%: 30.00"
    bad = _capital_invoice(discount_percentage=0.02)
    with pytest.raises(ValueError, match="Discount must be"):
        _normalize_discount(bad)


def test_reconciliation_rejects_bad_invoices_and_clears(
    tmp_path: Path,
) -> None:
    repo = ReconciliationRepository(init_db(tmp_path / "recon.db"))
    with pytest.raises(ValueError, match="Unknown supermarket"):
        repo.create_invoice(_capital_invoice(supermarket_name="Other"))
    with pytest.raises(ValueError, match="Choose a shop"):
        repo.create_invoice(_capital_invoice(shop="Hypermarket"))
    with pytest.raises(ValueError, match="Credit days"):
        repo.create_invoice(_capital_invoice(credit_days=-1))
    with pytest.raises(ValueError, match="Each category line needs a name"):
        repo.create_invoice(
            _capital_invoice(
                categories=[InvoiceCategory(category="", quantity=1, amount=1)]
            )
        )
    with pytest.raises(ValueError, match="Quantity cannot be negative"):
        repo.create_invoice(
            _capital_invoice(
                categories=[InvoiceCategory(category="Fish", quantity=-1, amount=1)]
            )
        )
    with pytest.raises(ValueError, match="Category amount cannot be negative"):
        repo.create_invoice(
            _capital_invoice(
                categories=[InvoiceCategory(category="Fish", quantity=1, amount=-1)]
            )
        )
    blank = InvoiceCategory(category="")
    with pytest.raises(ValueError, match="Choose a category"):
        _city_mart_line(blank)
    with pytest.raises(ValueError, match="Choose Piece or Pack"):
        _city_mart_line(
            InvoiceCategory(
                category="Snacks",
                subcategory="Chips",
                product_name="Potato",
                unit="Box",
                entered_quantity=1,
            )
        )
    with pytest.raises(ValueError, match="Quantity must be greater"):
        _city_mart_line(
            InvoiceCategory(
                category="Snacks",
                subcategory="Chips",
                product_name="Potato",
                unit="Piece",
                entered_quantity=0,
                pieces_per_pack=12,
                price_per_piece=1,
            )
        )
    with pytest.raises(ValueError, match="Pieces per pack"):
        _city_mart_line(
            InvoiceCategory(
                category="Snacks",
                subcategory="Chips",
                product_name="Potato",
                unit="Pack",
                entered_quantity=1,
                pieces_per_pack=0,
                price_per_piece=1,
            )
        )
    with pytest.raises(ValueError, match="Price cannot be negative"):
        _city_mart_line(
            InvoiceCategory(
                category="Snacks",
                subcategory="Chips",
                product_name="Potato",
                unit="Piece",
                entered_quantity=1,
                pieces_per_pack=1,
                price_per_piece=-1,
            )
        )
    with pytest.raises(ValueError, match="Add at least one product"):
        repo.create_invoice(
            _capital_invoice(
                supermarket_name="Capital",
                shop="CREL Distribution Center",
                categories=[InvoiceCategory(category="", unit="Piece")],
            )
        )
    exclusive = repo.create_invoice(
        _capital_invoice(
            invoice_number="M-EX",
            supermarket_name="Makro",
            shop="Makro-Sat Sun",
            tax_percentage=0,
            retro_percentage=0.05,
        )
    )
    assert exclusive.tax_treatment == "tax_exclusive"
    first = repo.create_invoice(_capital_invoice(invoice_number="KEEP"))
    second = repo.create_invoice(_capital_invoice(invoice_number="MOVE"))
    second.invoice_number = "KEEP"
    with pytest.raises(ValueError, match="already exists"):
        repo.update_invoice(second)
    missing = _capital_invoice(invoice_number="NO")
    with pytest.raises(ValueError, match="Invoice not found"):
        repo.update_invoice(missing)
    assert repo.delete_payment(999) is None
    assert repo.list_invoices("Capital", "Hyper") == []
    loaded = repo.terms_for(first)
    assert loaded.retro_percentage == 0.03
    invoices, payments = repo.clear_invoices_and_payments()
    assert invoices == 3
    assert payments == 0
    assert repo.list_invoices() == []


def test_product_rules(tmp_path: Path) -> None:
    repo = ReconciliationRepository(init_db(tmp_path / "products.db"))
    with pytest.raises(ValueError, match="product name are required"):
        repo.save_product(
            Product(
                category=" ",
                subcategory="A",
                name="B",
                pieces_per_pack=1,
                price_per_piece=1,
            )
        )
    with pytest.raises(ValueError, match="Pieces per pack"):
        repo.save_product(
            Product(
                category="A",
                subcategory="B",
                name="C",
                pieces_per_pack=0,
                price_per_piece=1,
            )
        )
    with pytest.raises(ValueError, match="Price cannot be negative"):
        repo.save_product(
            Product(
                category="A",
                subcategory="B",
                name="C",
                pieces_per_pack=1,
                price_per_piece=-1,
            )
        )
    saved = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato",
            pieces_per_pack=12,
            price_per_piece=10,
        )
    )
    saved.price_per_piece = 11
    updated = repo.save_product(saved)
    assert updated.price_per_piece == 11
    with pytest.raises(ValueError, match="already exists"):
        repo.save_product(
            Product(
                category="Snacks",
                subcategory="Chips",
                name="Potato",
                pieces_per_pack=6,
                price_per_piece=1,
            )
        )
    assert saved.id is not None
    repo.delete_product(saved.id)
    assert repo.list_products() == []


def test_used_product_is_hidden_and_the_invoice_keeps_its_copy(tmp_path: Path) -> None:
    repo = ReconciliationRepository(init_db(tmp_path / "soft-delete.db"))
    used = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=10,
        )
    )
    unused = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chocolate",
            name="Chocolate",
            pieces_per_pack=6,
            price_per_piece=20,
        )
    )
    invoice = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="SOFT-1",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 31),
            total_invoice_amount=1,
            categories=[
                InvoiceCategory(
                    category=used.category,
                    subcategory=used.subcategory,
                    product_name=used.name,
                    unit="Pack",
                    entered_quantity=2,
                    pieces_per_pack=used.pieces_per_pack,
                    price_per_piece=used.price_per_piece,
                )
            ],
        )
    )
    assert used.id is not None and unused.id is not None
    repo.delete_product(used.id)
    repo.delete_product(unused.id)
    assert repo.list_products() == []
    hidden = repo.list_products(active_only=False)
    assert [item.name for item in hidden] == ["Potato chips"]
    assert hidden[0].is_active is False
    stored = repo.get_invoice(invoice.id or 0)
    assert stored is not None
    assert stored.categories[0].product_name == "Potato chips"
    assert stored.categories[0].base_quantity == 24
    assert stored.categories[0].price_per_piece == 10
    assert stored.total_invoice_amount == 240
    items = repo.city_item_summary("Capital", "Hyper")
    assert items[0]["Product"] == "Potato chips"
    assert items[0]["Quantity (pieces)"] == 24
    restored = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=15,
        )
    )
    assert restored.is_active is True
    assert restored.price_per_piece == 15
    again = repo.get_invoice(invoice.id or 0)
    assert again is not None
    assert again.categories[0].price_per_piece == 10
    from bnadataentry.recon_ui import _products_for_line

    catalog = _products_for_line([], again.categories, 0)
    assert [item.name for item in catalog] == ["Potato chips"]
    assert _products_for_line([], again.categories, 1) == []
    invoices, payments, products = repo.clear_all_data()
    assert invoices == 1
    assert payments == 0
    assert products == 1
    assert repo.list_invoices() == []
    assert repo.list_products(active_only=False) == []


def test_office_repository_edges(tmp_path: Path) -> None:
    repo = InvoiceRepository(init_db(tmp_path / "office.db"))
    with pytest.raises(ValueError, match="invoice.id is required"):
        repo.update_invoice(Invoice(client_id="capital", invoice_no="1"))
    with pytest.raises(KeyError, match="not found"):
        repo.update_summary_overrides(99, remark="x")
    created = repo.create_invoice(_office_invoice("capital", "CREL", "88"))
    assert created.id is not None
    updated = repo.update_summary_overrides(
        created.id,
        tax_pct_override=0,
        retro_pct_override=3,
        remark="kept",
        branch_amounts={"CREL": 20},
        tax_amount=2,
        retro_amount=-1,
        delivery_charge=-4,
        balance_to_receive=15,
        recompute=False,
    )
    assert updated.summary.remark == "kept"
    assert updated.summary.delivery_charge == 4
    assert updated.summary.balance_to_receive == 15
    with pytest.raises(ValueError, match="invoice_id is required"):
        repo.create_payment(Payment(invoice_id=None, payment_date=date(2026, 3, 3), amount=1))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="greater than zero"):
        repo.create_payment(
            Payment(invoice_id=created.id, payment_date=date(2026, 3, 3), amount=0)
        )
    with pytest.raises(ValueError, match="payment_date is required"):
        repo.create_payment(
            Payment(invoice_id=created.id, payment_date=None, amount=1)  # type: ignore[arg-type]
        )
    paid = repo.create_payment(
        Payment(invoice_id=created.id, payment_date=date(2026, 3, 3), amount=5)
    )
    assert repo.get_payment(999) is None
    assert repo.list_payments_for_invoices([]) == []
    assert repo.sum_paid_by_invoice_ids([]) == {}
    assert repo.count_invoices() == 1
    assert repo.count_line_items() == 2
    with pytest.raises(ValueError, match="payment.id is required"):
        repo.update_payment(Payment(invoice_id=created.id, payment_date=date(2026, 3, 3), amount=1))
    missing = Payment(
        invoice_id=created.id,
        payment_date=date(2026, 3, 4),
        amount=2,
        id=999,
    )
    with pytest.raises(ValueError, match="not found"):
        repo.update_payment(missing)
    paid.amount = 6
    assert repo.update_payment(paid).amount == 6
    undated = repo.create_invoice(
        Invoice(client_id="makro", invoice_no="NO-DATE", branch="Sat San")
    )
    assert undated.po_date is None
    listed = repo.list_invoices(client_id="makro", year=2026)
    assert listed == []


def test_old_database_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE clients (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            default_tax_pct REAL NOT NULL DEFAULT 0,
            default_retro_pct REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY,
            client_id TEXT NOT NULL,
            invoice_no TEXT NOT NULL,
            po_no TEXT NOT NULL DEFAULT '',
            po_date TEXT,
            invoice_date TEXT,
            due_date TEXT,
            branch TEXT NOT NULL DEFAULT '',
            product_label TEXT NOT NULL DEFAULT '',
            UNIQUE (client_id, invoice_no, invoice_date)
        );
        CREATE TABLE line_items (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL,
            product_description TEXT NOT NULL,
            pack_qty REAL NOT NULL DEFAULT 0,
            list_price REAL NOT NULL DEFAULT 0,
            line_total REAL NOT NULL DEFAULT 0,
            discount_amount REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            grand_total REAL NOT NULL DEFAULT 0,
            is_foc INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE invoice_summary (
            invoice_id INTEGER PRIMARY KEY,
            branch_amounts_json TEXT NOT NULL DEFAULT '{}',
            tax_amount REAL,
            retro_amount REAL,
            balance_to_receive REAL,
            remark TEXT NOT NULL DEFAULT '',
            tax_pct_override REAL,
            retro_pct_override REAL
        );
        CREATE INDEX idx_invoices_branch ON invoices(branch);
        CREATE TABLE supermarket_invoices (
            id INTEGER PRIMARY KEY,
            invoice_number TEXT NOT NULL UNIQUE,
            supermarket_name TEXT NOT NULL,
            invoice_date TEXT NOT NULL,
            due_date TEXT NOT NULL,
            total_invoice_amount REAL NOT NULL,
            is_tax_included INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Pending'
        );
        CREATE TABLE supermarket_payments (
            id INTEGER PRIMARY KEY,
            invoice_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            actual_received_amount REAL NOT NULL,
            retro_percentage REAL NOT NULL DEFAULT 0,
            delivery_charge_percentage REAL NOT NULL DEFAULT 0
        );
        INSERT INTO supermarket_invoices (
            id, invoice_number, supermarket_name, invoice_date, due_date,
            total_invoice_amount, is_tax_included, status
        ) VALUES
            (1, 'OLD-C', 'Capital', '2026-01-01', '2026-02-01', 100, 1, 'Pending'),
            (2, 'OLD-M', 'Makro', '2026-01-01', '2026-02-01', 50, 0, 'Pending');
        """
    )
    raw.commit()
    raw.close()

    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    empty.execute(
        """
        CREATE TABLE supermarket_invoices (
            id INTEGER PRIMARY KEY,
            supermarket_name TEXT,
            is_tax_included INTEGER
        )
        """
    )
    _ensure_supermarket_shop_column(empty)
    _ensure_tax_treatment_column(empty)
    _ensure_makro_rate_columns(empty)
    _ensure_invoice_category_columns(empty)
    _ensure_commercial_columns(empty)
    _ensure_payments_table(empty)
    empty.execute(
        """
        CREATE TABLE invoice_summary (
            invoice_id INTEGER PRIMARY KEY,
            branch_amounts_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    _ensure_delivery_charge_column(empty)
    _add_column(empty, "invoice_summary", "delivery_charge", "delivery_charge REAL")
    empty.close()

    conn = init_db(path)
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_invoices)")
    }
    assert {"shop", "tax_treatment", "discount_percentage", "credit_days"} <= columns
    copied = conn.execute("SELECT COUNT(*) AS n FROM invoice_categories").fetchone()
    assert copied["n"] == 0
    summary_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(invoice_summary)")
    }
    assert "delivery_charge" in summary_cols
    line_cols = {row["name"] for row in conn.execute("PRAGMA table_info(line_items)")}
    assert "pack_qty_fa" in line_cols
    conn.close()


def test_excel_round_trip_and_header_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aliases = {
        "Due Date (Payment)": "due_date",
        "Invoice Date extra": "invoice_date",
        "Invoice No.": "invoice_no",
        "Product Description": "product",
        "Packs": "pack_qty",
        "List Price MMK": "list_price",
        "Grand Total Amount": "grand_total",
        "Discount 3%": "discount",
        "Tax": "tax",
        "Retro 4%": "retro",
        "Balance due": "balance",
        "PODate": "po_date",
        "Total Invoice": "total",
        "Total": "total",
        "CREL amount": "crel",
        "DC MDY 6% other": "dc_charge",
        "DC MDY": "dc_mdy",
        "Hyper shop": "hyper",
        "G & G": "g_and_g",
        "Sat San": "sat_san",
        "Kha Wae": "kha_wae_chan",
        "Remark text": "remark",
        "Amount": "amount",
        "PO Date": "po_date",
        "PO No 12": "po_no",
    }
    for raw, key in aliases.items():
        assert _normalize_header(raw) == key
    assert _fmt_summary_date("not-a-date") == "not-a-date"

    monkeypatch.setattr(
        "bnadataentry.excel_export.EXPORTS_DIR", tmp_path / "exports"
    )
    source = InvoiceRepository(init_db(tmp_path / "export.db"))
    for client_id, branch, number in (
        ("capital", "CREL", "C1"),
        ("makro", "Sat San", "M1"),
        ("one_stop_mart", "One Stop Mart", "O1"),
    ):
        source.create_invoice(_office_invoice(client_id, branch, number))
    capital_book = export_client_workbook(
        source, "capital", tmp_path / "Capital 2026.xlsx", year=2026, month=3
    )
    makro_book = export_client_workbook(source, "makro", tmp_path / "Makro.xlsx")
    osm_book = export_client_workbook(
        source, "one_stop_mart", tmp_path / "One Stop Mart.xlsx", year_from=2026, year_to=2027
    )
    default_book = export_client_workbook(source, "capital", year=2026)
    assert capital_book.exists() and makro_book.exists() and osm_book.exists()
    assert default_book.parent.name == "exports"
    default_book.unlink()

    imported = InvoiceRepository(init_db(tmp_path / "import.db"))
    result = import_workbook(capital_book, imported)
    assert result.invoices_upserted == 1
    assert result.line_items >= 1
    found = imported.find_by_key("capital", "C1", date(2026, 3, 2), branch="CREL")
    assert found is not None
    assert found.lines[0].product_description == "Sardine"

    rows = load_excel_summary_rows("capital", year=2026, month=3, excel_dir=tmp_path)
    assert rows and rows[0]["Invoice No"] == "C1"
    assert load_excel_summary_rows("makro", excel_dir=tmp_path / "missing") == []

    (tmp_path / "~$Capital.xlsx").write_bytes(b"lock")
    all_results = import_all_excel_data(imported, tmp_path)
    assert len(all_results) == 3

    uploads = import_uploaded_workbooks(
        imported,
        [
            ("notes.txt", b"no"),
            ("old.xls", b"no"),
            ("random.xlsx", b"no"),
            ("Capital copy.xlsx", capital_book.read_bytes()),
            ("Capital copy.xlsx", capital_book.read_bytes()),
        ],
        staging_dir=tmp_path / "uploads",
    )
    assert any("expected a .xlsx" in item.errors[0].message for item in uploads)
    assert any("legacy .xls" in item.errors[0].message for item in uploads)
    assert any("cannot detect client" in item.errors[0].message for item in uploads)
    assert sum(item.invoices_upserted for item in uploads) == 2

    overlay_invoice = _office_invoice("capital", "CREL", "C1")
    overlay_invoice.product_label = ""
    overlay_invoice.po_date = None
    _apply_summary_overlay(
        overlay_invoice,
        {
            "remark": "from summary",
            "product_label": "Sardine",
            "po_date": date(2026, 3, 1),
            "due_date": date(2026, 4, 2),
            "branch_amounts": {"CREL": 20},
            "tax_amount": 0,
            "retro_amount": -1,
            "delivery_charge": 2,
            "balance_to_receive": 17,
        },
    )
    assert overlay_invoice.summary.remark == "from summary"
    assert overlay_invoice.summary.delivery_charge == 2
    assert overlay_invoice.summary.balance_to_receive == 19
    assert _summary_overlay_matches(
        overlay_invoice,
        {"invoice_no": "C1", "invoice_date": date(2026, 3, 2), "branch_amounts": {"CREL": 20}},
    )
    assert not _summary_overlay_matches(
        overlay_invoice,
        {"invoice_no": "OTHER", "invoice_date": date(2026, 3, 2)},
    )


def test_ui_helper_templates() -> None:
    assert "Makro" in client_template_blurb("makro")
    assert "One Stop Mart" in client_template_blurb("one_stop_mart")
    assert "ဖာ" in pack_qty_editor_labels("makro")["pack_qty_fa"]
    assert "Pk (pcs)" in pack_qty_editor_labels("one_stop_mart")["pack_qty"]
    assert empty_detail_line("makro")["is_foc"] is False
    assert "pack_qty_fa" in empty_detail_line("capital")
    editor = invoice_lines_for_editor(_office_invoice("makro", "Sat San", "1"), "makro")
    assert editor[1]["is_foc"] is True
    assert invoice_lines_for_editor(None, "one_stop_mart")[0]["pack_qty"] == 0
    prepared = apply_line_totals(
        [{"product_description": "Fish", "pack_qty": 2, "list_price": 10}],
        client_id="makro",
    )
    assert prepared[0]["tax_amount"] == pytest.approx(1)
    assert line_totals_footer(prepared)["tax_total"] == pytest.approx(1)
    invoice = _office_invoice("capital", "CREL", "1")
    invoice.summary.balance_to_receive = None
    capital_rows = summary_sheet_rows([invoice], "capital")
    assert capital_rows[0]["Invoice No"] == "1"
    assert summary_columns("makro")[0] == "PO Date"
    makro_rows = summary_sheet_rows(
        [_office_invoice("makro", "Sat San", "2")], "makro"
    )
    assert makro_rows[0]["Product Description"] == "Sardine +1 more"
    osm_rows = summary_sheet_rows(
        [_office_invoice("one_stop_mart", "One Stop Mart", "3")],
        "one_stop_mart",
    )
    assert osm_rows[0]["Total Invoice"] == pytest.approx(20)
    assert browse_line_rows(invoice, "makro")[1]["Total Invoice"] == "FOC"
    assert "Pack Qty (Pk (pcs))" in browse_line_rows(invoice, "one_stop_mart")[0]
    table = browse_table_rows([invoice], "one_stop_mart")
    assert table[-1]["Invoice No"] == "Total"
    specs = browse_metric_specs(
        "makro",
        {
            "merchandise_total": 20,
            "tax_total": 1,
            "grand_total": 21,
            "discount_total": 0,
            "retro": -1,
            "delivery_charge": 0,
            "balance_to_receive": 20,
        },
    )
    assert specs[1][0] == "Tax 5%"
    osm_specs = browse_metric_specs(
        "one_stop_mart",
        {
            "merchandise_total": 20,
            "discount_total": 1,
            "grand_total": 19,
            "retro": -1,
            "delivery_charge": 0,
            "balance_to_receive": 19,
        },
    )
    assert osm_specs[1][0].startswith("Discount")
    preview = preview_invoice_totals(invoice)
    assert preview["merchandise_total"] == pytest.approx(20)
