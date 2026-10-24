"""Expected-payment formulas and reconciliation persistence."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bnadataentry.db import init_db
from bnadataentry.expected_payment import PaymentTerms, expected_payment, money
from bnadataentry.reconciliation import (
    STATUS_PAID,
    STATUS_PARTIAL,
    STATUS_PENDING,
    InvoiceCategory,
    Product,
    ReconciliationRepository,
    SupermarketInvoice,
    SupermarketPayment,
    payment_status,
)


def test_capital_tax_included_applies_retro_then_ex_tax_then_delivery() -> None:
    result = expected_payment(
        "Capital",
        PaymentTerms(
            total_invoice_amount=100_000,
            is_tax_included=True,
            retro_percentage=0.03,
            delivery_charge_percentage=0.04,
        ),
    )
    balance = 100_000 * 0.97
    base = (balance / 105) * 100
    assert result.expected_final_payment == money(balance - base * 0.04)


def test_capital_always_uses_the_tax_included_delivery_formula() -> None:
    result = expected_payment(
        "Capital",
        PaymentTerms(
            total_invoice_amount=100_000,
            is_tax_included=False,
            retro_percentage=0.03,
            delivery_charge_percentage=0.06,
        ),
    )
    balance = 97_000
    base = (balance / 105) * 100
    assert result.expected_final_payment == money(balance - base * 0.06)


def test_capital_retro_percent_is_the_rate_entered() -> None:
    result = expected_payment(
        "Capital",
        PaymentTerms(
            total_invoice_amount=100_000,
            is_tax_included=False,
            retro_percentage=0.04,
            delivery_charge_percentage=0.0,
        ),
    )
    assert result.expected_final_payment == money(100_000 * 0.96)


def test_makro_tax_included_adds_tax_then_deducts_retro() -> None:
    result = expected_payment(
        "Makro",
        PaymentTerms(
            total_invoice_amount=200_000,
            tax_percentage=0.05,
            retro_percentage=0.05,
        ),
    )
    grand = 200_000 * 1.05
    assert result.expected_final_payment == money(grand - grand * 0.05)


def test_makro_tax_exclusive_deducts_retro_without_extra_tax() -> None:
    result = expected_payment(
        "Makro",
        PaymentTerms(
            total_invoice_amount=200_000,
            tax_percentage=0.0,
            retro_percentage=0.05,
        ),
    )
    assert result.expected_final_payment == money(200_000 * 0.95)


def test_makro_no_tax_no_retro_keeps_the_invoice_total() -> None:
    result = expected_payment(
        "Makro",
        PaymentTerms(
            total_invoice_amount=200_000,
            tax_percentage=0.0,
            retro_percentage=0.0,
        ),
    )
    assert result.expected_final_payment == money(200_000)


def test_makro_uses_the_tax_and_retro_percents_entered() -> None:
    result = expected_payment(
        "Makro",
        PaymentTerms(
            total_invoice_amount=100_000,
            tax_percentage=0.07,
            retro_percentage=0.04,
        ),
    )
    grand = 100_000 * 1.07
    assert result.expected_final_payment == money(grand * 0.96)


def test_one_stop_mart_combines_back_margin_and_delivery_incentive() -> None:
    result = expected_payment(
        "One Stop Mart",
        PaymentTerms(
            total_invoice_amount=80_000,
            retro_percentage=0.03,
            delivery_charge_percentage=0.02,
        ),
    )
    assert result.expected_final_payment == money(80_000 * 0.95)


def test_payment_status_bands() -> None:
    assert payment_status(1000, 0) == STATUS_PENDING
    assert payment_status(1000, 400) == STATUS_PARTIAL
    assert payment_status(1000, 1000) == STATUS_PAID
    assert payment_status(1000, 1100) == STATUS_PAID


@pytest.fixture
def repo(tmp_path: Path) -> ReconciliationRepository:
    conn = init_db(tmp_path / "recon.db")
    return ReconciliationRepository(conn)


def test_invoice_payment_and_report(repo: ReconciliationRepository) -> None:
    invoice = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="C-1001",
            supermarket_name="Capital",
            shop="CREL Distribution Center",
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 10, 1),
            total_invoice_amount=100_000,
            is_tax_included=True,
        )
    )
    assert invoice.id is not None
    assert invoice.status == STATUS_PENDING
    assert invoice.tax_treatment == "tax_included"
    before = repo.report_rows("Capital")[0]
    assert before["Expected Final Payment"] == money(97_000)
    assert len(repo.list_invoices("Capital", "CREL Distribution Center")) == 1
    assert repo.list_invoices("Capital", "Hyper") == []

    with pytest.raises(ValueError, match="already exists"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="C-1001",
                supermarket_name="Makro",
                shop="Makro-Sat Sun",
                invoice_date=date(2026, 9, 2),
                due_date=date(2026, 10, 2),
                total_invoice_amount=50,
            )
        )

    balance = 97_000
    base = (balance / 105) * 100
    expected = money(balance - base * 0.04)
    repo.create_payment(
        SupermarketPayment(
            invoice_id=invoice.id,
            payment_date=date(2026, 9, 20),
            actual_received_amount=expected - 500,
            retro_percentage=0.03,
            delivery_charge_percentage=0.04,
        )
    )
    row = repo.report_rows("Capital")[0]
    assert row["Expected Final Payment"] == expected
    assert row["Variance"] == money(-500)
    assert row["Status"] == STATUS_PARTIAL

    stored = repo.get_invoice(invoice.id)
    assert stored is not None
    assert stored.status == STATUS_PARTIAL

    invoice.total_invoice_amount = 80_000
    invoice.is_tax_included = False
    invoice.tax_treatment = "tax_exclusive"
    invoice.categories = [
        InvoiceCategory(category=" Sardines ", quantity=80, amount=50_000),
        InvoiceCategory(category="Sardines", quantity=40, amount=0),
        InvoiceCategory(category="Yobick", quantity=10, amount=30_000),
    ]
    invoice.invoice_number = "C-1001-EDITED"
    updated = repo.update_invoice(invoice)
    assert updated.invoice_number == "C-1001-EDITED"
    assert updated.total_invoice_amount == 80_000
    assert updated.is_tax_included is True
    assert updated.tax_treatment == "tax_included"
    assert updated.categories == [
        InvoiceCategory(category="Sardines", quantity=120, amount=50_000),
        InvoiceCategory(category="Yobick", quantity=10, amount=30_000),
    ]
    stored = repo.get_invoice(invoice.id)
    assert stored is not None
    assert stored.invoice_number == "C-1001-EDITED"
    assert stored.categories == updated.categories
    assert stored.status == STATUS_PAID

    makro = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="M-200",
            supermarket_name="Makro",
            shop="Makro-Sat Sun",
            invoice_date=date(2026, 9, 3),
            due_date=date(2026, 10, 3),
            total_invoice_amount=100_000,
            tax_percentage=0.07,
            retro_percentage=0.04,
        )
    )
    assert makro.id is not None
    makro_row = repo.report_rows("Makro", "Makro-Sat Sun")[0]
    assert makro_row["Expected Final Payment"] == money(100_000 * 1.07 * 0.96)
    assert makro_row["Tax %"] == 7
    assert makro_row["Retro %"] == 4
    repo.create_payment(
        SupermarketPayment(
            invoice_id=makro.id,
            payment_date=date(2026, 9, 21),
            actual_received_amount=1000,
            tax_percentage=0.05,
            retro_percentage=0.05,
        )
    )
    updated_makro = repo.get_invoice(makro.id)
    assert updated_makro is not None
    assert updated_makro.tax_percentage == 0.05
    assert updated_makro.retro_percentage == 0.05

    repo.create_invoice(
        SupermarketInvoice(
            invoice_number="C-200",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=date(2026, 9, 4),
            due_date=date(2026, 10, 4),
            total_invoice_amount=50_000,
            categories=[
                InvoiceCategory(category="Sardines", quantity=20, amount=15_000),
                InvoiceCategory(category="Yobick", quantity=8, amount=35_000),
            ],
        )
    )
    summary, missing = repo.category_summary("Capital", "Hyper")
    assert missing == 0
    assert summary == [
        {
            "Category": "Sardines",
            "Quantity": 20.0,
            "Total amount": money(15_000),
            "Invoices": 1,
        },
        {
            "Category": "Yobick",
            "Quantity": 8.0,
            "Total amount": money(35_000),
            "Invoices": 1,
        },
    ]
    crel, crel_missing = repo.category_summary("Capital", "CREL Distribution Center")
    assert crel_missing == 0
    assert crel == [
        {
            "Category": "Sardines",
            "Quantity": 120.0,
            "Total amount": money(50_000),
            "Invoices": 1,
        },
        {
            "Category": "Yobick",
            "Quantity": 10.0,
            "Total amount": money(30_000),
            "Invoices": 1,
        },
    ]
    assert repo.category_summary("Capital", "G&G") == ([], 0)

    shared = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="C-300",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=date(2026, 9, 5),
            due_date=date(2026, 10, 5),
            total_invoice_amount=10_000,
            categories=[InvoiceCategory(category="Sardines", quantity=4)],
        )
    )
    assert shared.categories == [
        InvoiceCategory(category="Sardines", quantity=4, amount=10_000)
    ]
    with pytest.raises(ValueError, match="needs a name"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="C-301",
                supermarket_name="Capital",
                shop="Hyper",
                invoice_date=date(2026, 9, 6),
                due_date=date(2026, 10, 6),
                total_invoice_amount=1_000,
                categories=[InvoiceCategory(category="  ", quantity=2, amount=1_000)],
            )
        )
    combined, combined_missing = repo.category_summary("Capital", "Hyper")
    assert combined_missing == 0
    assert combined[0] == {
        "Category": "Sardines",
        "Quantity": 24.0,
        "Total amount": money(25_000),
        "Invoices": 2,
    }

    invoices, payments = repo.clear_invoices_and_payments()
    assert invoices == 4
    assert payments == 2
    assert repo.list_invoices() == []
    assert repo.list_payments(invoice.id) == []


def test_capital_discount_is_taken_before_retro() -> None:
    result = expected_payment(
        "Capital",
        PaymentTerms(
            total_invoice_amount=100_000,
            retro_percentage=0.03,
            delivery_charge_percentage=0.0,
            discount_percentage=0.03,
        ),
    )
    assert result.expected_final_payment == money(100_000 * 0.97 * 0.97)


def test_capital_tax_exempt_delivery_skips_the_ex_tax_step() -> None:
    result = expected_payment(
        "Capital",
        PaymentTerms(
            total_invoice_amount=100_000,
            retro_percentage=0.03,
            delivery_charge_percentage=0.04,
            exempt_amount=40_000,
        ),
    )
    balance = 97_000
    exempt_balance = balance * 0.4
    taxable_balance = balance - exempt_balance
    taxable_delivery = ((taxable_balance / 105) * 100) * 0.04
    exempt_delivery = exempt_balance * 0.04
    assert result.expected_final_payment == money(
        balance - taxable_delivery - exempt_delivery
    )


def test_city_mart_pack_lines_credit_days_and_separate_reports(
    repo: ReconciliationRepository,
) -> None:
    chips = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=10,
        )
    )
    chocolate = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chocolate",
            name="Chocolate",
            pieces_per_pack=6,
            price_per_piece=20,
        )
    )
    invoice_date = date(2026, 10, 1)
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="CM-1",
            supermarket_name="Capital",
            shop="CREL Distribution Center",
            invoice_date=invoice_date,
            due_date=date(2026, 10, 8),
            total_invoice_amount=1,
            credit_days=7,
            categories=[
                InvoiceCategory(
                    category=chips.category,
                    subcategory=chips.subcategory,
                    product_name=chips.name,
                    unit="Pack",
                    entered_quantity=2,
                    pieces_per_pack=chips.pieces_per_pack,
                    price_per_piece=chips.price_per_piece,
                ),
                InvoiceCategory(
                    category=chocolate.category,
                    subcategory=chocolate.subcategory,
                    product_name=chocolate.name,
                    unit="Piece",
                    entered_quantity=5,
                    pieces_per_pack=chocolate.pieces_per_pack,
                    price_per_piece=chocolate.price_per_piece,
                ),
            ],
        )
    )
    assert saved.due_date == date(2026, 10, 8)
    assert saved.total_invoice_amount == money(2 * 10 * 12 + 5 * 20)
    assert saved.categories[0].base_quantity == 24
    assert saved.categories[0].amount == money(240)
    assert saved.categories[1].base_quantity == 5
    chips.price_per_piece = 99
    repo.save_product(chips)
    stored = repo.get_invoice(saved.id)
    assert stored is not None
    assert stored.categories[0].price_per_piece == 10
    row = repo.report_rows("Capital", "CREL Distribution Center")[0]
    assert row["Expected Final Payment"] == money(saved.total_invoice_amount * 0.97)
    amounts, missing = repo.city_category_summary("Capital", "CREL Distribution Center")
    assert missing == 0
    assert amounts == [
        {
            "Category": "Snacks",
            "Sub-category": "Chips",
            "Total amount": money(240),
        },
        {
            "Category": "Snacks",
            "Sub-category": "Chocolate",
            "Total amount": money(100),
        },
    ]
    items = {str(item["Product"]): item for item in repo.city_item_summary("Capital", "CREL Distribution Center")}
    assert items["Potato chips"]["Quantity (pieces)"] == 24
    assert items["Chocolate"]["Quantity (pieces)"] == 5
    assert saved.id is not None
    repo.delete_invoice(saved.id)
    assert repo.get_invoice(saved.id) is None
    assert repo.list_products()[0].price_per_piece == 99


def test_capital_discount_remark_exempt_row_and_delete(
    repo: ReconciliationRepository,
) -> None:
    invoice = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="C-DISC",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 8),
            total_invoice_amount=100_000,
            discount_percentage=0.03,
            credit_days=7,
            categories=[
                InvoiceCategory(category="Dairy", quantity=10, amount=40_000, tax_exempt=True),
                InvoiceCategory(category="Grocery", quantity=4, amount=60_000),
            ],
        )
    )
    assert invoice.remark == "Discount 3%: 3,000.00"
    assert invoice.due_date == date(2026, 10, 8)
    assert invoice.id is not None
    repo.create_payment(
        SupermarketPayment(
            invoice_id=invoice.id,
            payment_date=date(2026, 10, 2),
            actual_received_amount=1,
            retro_percentage=0.03,
            delivery_charge_percentage=0.04,
        )
    )
    balance = 100_000 * 0.97 * 0.97
    exempt_balance = balance * 0.4
    taxable_balance = balance - exempt_balance
    expected = money(
        balance
        - ((taxable_balance / 105) * 100) * 0.04
        - exempt_balance * 0.04
    )
    row = repo.report_rows("Capital", "Hyper")[0]
    assert row["Expected Final Payment"] == expected
    kept = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="C-KEEP",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=date(2026, 10, 3),
            due_date=date(2026, 11, 2),
            total_invoice_amount=500,
        )
    )
    repo.delete_invoice(invoice.id)
    assert repo.get_invoice(invoice.id) is None
    assert repo.list_payments(invoice.id) == []
    remaining = repo.list_invoices("Capital", "Hyper")
    assert [item.invoice_number for item in remaining] == ["C-KEEP"]
    assert kept.id is not None


def test_makro_pack_line_is_stored_as_pieces(repo: ReconciliationRepository) -> None:
    chips = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=10,
        )
    )
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="M-PACK",
            supermarket_name="Makro",
            shop="Makro-Sat Sun",
            invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 31),
            total_invoice_amount=1,
            tax_percentage=0.05,
            retro_percentage=0.05,
            categories=[
                InvoiceCategory(
                    category=chips.category,
                    subcategory=chips.subcategory,
                    product_name=chips.name,
                    unit="Pack",
                    entered_quantity=2,
                    pieces_per_pack=chips.pieces_per_pack,
                    price_per_piece=chips.price_per_piece,
                )
            ],
        )
    )
    assert saved.categories[0].base_quantity == 24
    assert saved.categories[0].amount == 240
    assert saved.total_invoice_amount == 240
    row = repo.report_rows("Makro", "Makro-Sat Sun")[0]
    assert row["Expected Final Payment"] == money(240 * 1.05 * 0.95)


def test_makro_discount_comes_off_before_tax_and_retro() -> None:
    result = expected_payment(
        "Makro",
        PaymentTerms(
            total_invoice_amount=100_000,
            tax_percentage=0.05,
            retro_percentage=0.05,
            discount_percentage=0.03,
        ),
    )
    net = 100_000 * 0.97
    grand = net * 1.05
    assert result.expected_final_payment == money(grand * 0.95)


def test_makro_tax_exempt_share_skips_the_tax_percent(
    repo: ReconciliationRepository,
) -> None:
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="M-EXEMPT",
            supermarket_name="Makro",
            shop="Makro-Sat Sun",
            invoice_date=date(2026, 10, 2),
            due_date=date(2026, 11, 1),
            total_invoice_amount=100_000,
            tax_percentage=0.05,
            retro_percentage=0.05,
            categories=[
                InvoiceCategory(
                    category="Dairy",
                    quantity=1,
                    amount=40_000,
                    tax_exempt=True,
                ),
                InvoiceCategory(category="Snacks", quantity=1, amount=60_000),
            ],
        )
    )
    assert saved.categories[0].tax_exempt is True
    taxable = 60_000
    exempt = 40_000
    grand = taxable * 1.05 + exempt
    row = repo.report_rows("Makro", "Makro-Sat Sun")[0]
    assert row["Expected Final Payment"] == money(grand * 0.95)


def test_one_stop_mart_discount_comes_off_before_back_margin() -> None:
    result = expected_payment(
        "One Stop Mart",
        PaymentTerms(
            total_invoice_amount=80_000,
            retro_percentage=0.03,
            delivery_charge_percentage=0.02,
            discount_percentage=0.03,
        ),
    )
    net = 80_000 * 0.97
    assert result.expected_final_payment == money(net * 0.95)


def test_one_stop_mart_discount_is_kept_and_reports_by_product(
    repo: ReconciliationRepository,
) -> None:
    chips = repo.save_product(
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=10,
        )
    )
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="O-DISC",
            supermarket_name="One Stop Mart",
            shop="One Stop Mart",
            invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 31),
            total_invoice_amount=1,
            discount_percentage=0.03,
            categories=[
                InvoiceCategory(
                    category=chips.category,
                    subcategory=chips.subcategory,
                    product_name=chips.name,
                    unit="Pack",
                    entered_quantity=2,
                    pieces_per_pack=chips.pieces_per_pack,
                    price_per_piece=chips.price_per_piece,
                    tax_exempt=True,
                )
            ],
        )
    )
    assert saved.discount_percentage == 0.03
    assert saved.remark == "Discount 3%: 7.20"
    assert saved.categories[0].tax_exempt is True
    assert saved.total_invoice_amount == 240
    repo.create_payment(
        SupermarketPayment(
            invoice_id=saved.id or 0,
            payment_date=date(2026, 10, 2),
            actual_received_amount=200,
            retro_percentage=0.03,
            delivery_charge_percentage=0.02,
        )
    )
    row = repo.report_rows("One Stop Mart", "One Stop Mart")[0]
    net = 240 * 0.97
    assert row["Expected Final Payment"] == money(net * 0.95)
    amounts, _missing = repo.city_category_summary("One Stop Mart", "One Stop Mart")
    assert amounts[0]["Total amount"] == 240
    items = repo.city_item_summary("One Stop Mart", "One Stop Mart")
    assert items[0]["Quantity (pieces)"] == 24

