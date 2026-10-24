"""Business-rule tests for the Phase 2 calculation and invoice rules.

Run locally:

    .venv/bin/python -m pytest tests/test_requirements_3_2.py -q

Each case uses a temporary database. The live data/app.db file is not touched.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from bnadataentry.db import init_db
from bnadataentry.expected_payment import PaymentTerms, expected_payment, money
from bnadataentry.reconciliation import (
    InvoiceCategory,
    Product,
    ReconciliationRepository,
    SupermarketInvoice,
    SupermarketPayment,
)

INVOICE_DATE = date(2026, 10, 1)
SHOPS = {
    "Capital": "Hyper",
    "Makro": "Makro-Sat Sun",
    "One Stop Mart": "One Stop Mart",
}


@pytest.fixture
def repo(tmp_path: Path) -> ReconciliationRepository:
    return ReconciliationRepository(init_db(tmp_path / "rules.db"))


def _pay(total: float, **rates: float) -> PaymentTerms:
    return PaymentTerms(total_invoice_amount=total, **rates)


def test_capital_taxable_delivery_uses_the_ex_tax_base() -> None:
    """Tax is already in the price. Retro is dynamic, then DC uses /105."""
    total = 100_000.0
    retro = 0.03
    delivery = 0.04
    result = expected_payment(
        "Capital",
        _pay(
            total,
            is_tax_included=True,
            retro_percentage=retro,
            delivery_charge_percentage=delivery,
            exempt_amount=0,
        ),
    )
    balance = total * (1 - retro)
    delivery_amount = ((balance / 105) * 100) * delivery
    assert result.expected_final_payment == money(balance - delivery_amount)
    assert result.expected_final_payment != money(balance - balance * delivery)


@pytest.mark.parametrize("retro", [0.0, 0.03, 0.04, 0.075])
@pytest.mark.parametrize("delivery", [0.0, 0.04, 0.06])
def test_capital_retro_and_delivery_follow_the_entered_rates(
    retro: float, delivery: float
) -> None:
    total = 100_000.0
    result = expected_payment(
        "Capital",
        _pay(total, retro_percentage=retro, delivery_charge_percentage=delivery),
    )
    balance = total * (1 - retro)
    assert result.expected_final_payment == money(
        balance - ((balance / 105) * 100) * delivery
    )


def test_capital_tax_exempt_invoice_uses_balance_times_delivery() -> None:
    """A fully tax-exempt invoice skips the divide-by-105 step."""
    total = 100_000.0
    retro = 0.03
    delivery = 0.06
    result = expected_payment(
        "Capital",
        _pay(
            total,
            retro_percentage=retro,
            delivery_charge_percentage=delivery,
            exempt_amount=total,
        ),
    )
    balance = total * (1 - retro)
    assert result.expected_final_payment == money(balance - balance * delivery)


def test_capital_mixed_invoice_splits_the_delivery_formula() -> None:
    total = 100_000.0
    exempt = 40_000.0
    retro = 0.03
    delivery = 0.04
    result = expected_payment(
        "Capital",
        _pay(
            total,
            retro_percentage=retro,
            delivery_charge_percentage=delivery,
            exempt_amount=exempt,
        ),
    )
    balance = total * (1 - retro)
    exempt_balance = balance * (exempt / total)
    taxable_balance = balance - exempt_balance
    expected = balance - ((taxable_balance / 105) * 100) * delivery
    expected -= exempt_balance * delivery
    assert result.expected_final_payment == money(expected)


def test_makro_adds_five_percent_tax_then_deducts_five_percent_retro() -> None:
    total = 200_000.0
    result = expected_payment(
        "Makro",
        _pay(total, tax_percentage=0.05, retro_percentage=0.05),
    )
    grand = total + total * 0.05
    assert result.expected_final_payment == money(grand - grand * 0.05)


def test_makro_tax_exempt_items_do_not_receive_the_five_percent_tax() -> None:
    total = 100_000.0
    exempt = 40_000.0
    taxable = total - exempt
    result = expected_payment(
        "Makro",
        _pay(
            total,
            tax_percentage=0.05,
            retro_percentage=0.05,
            exempt_amount=exempt,
        ),
    )
    grand = taxable + taxable * 0.05 + exempt
    whole_invoice_taxed = (total + total * 0.05) * 0.95
    assert result.expected_final_payment == money(grand * 0.95)
    assert result.expected_final_payment != money(whole_invoice_taxed)


def test_makro_fully_exempt_invoice_keeps_the_total_as_the_grand_total() -> None:
    total = 80_000.0
    result = expected_payment(
        "Makro",
        _pay(total, tax_percentage=0.05, retro_percentage=0.05, exempt_amount=total),
    )
    assert result.expected_final_payment == money(total * 0.95)


def test_one_stop_mart_combines_dynamic_rates_and_adds_no_tax() -> None:
    total = 80_000.0
    back_margin = 0.03
    delivery_incentive = 0.02
    result = expected_payment(
        "One Stop Mart",
        _pay(
            total,
            retro_percentage=back_margin,
            delivery_charge_percentage=delivery_incentive,
            exempt_amount=20_000,
        ),
    )
    combined = back_margin + delivery_incentive
    assert combined != 0.10
    assert result.expected_final_payment == money(total * (1 - combined))
    taxed = (total * 1.05) * (1 - combined)
    assert result.expected_final_payment != money(taxed)


@pytest.mark.parametrize(
    ("back_margin", "delivery_incentive"),
    [(0.05, 0.05), (0.07, 0.04), (0.0, 0.015)],
)
def test_one_stop_mart_rates_are_not_locked_at_ten_percent(
    back_margin: float, delivery_incentive: float
) -> None:
    total = 50_000.0
    result = expected_payment(
        "One Stop Mart",
        _pay(
            total,
            retro_percentage=back_margin,
            delivery_charge_percentage=delivery_incentive,
        ),
    )
    combined = back_margin + delivery_incentive
    assert result.expected_final_payment == money(total - total * combined)


def test_two_packs_of_twelve_are_stored_as_twenty_four_pieces(
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
            invoice_number="PACK-2",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=30),
            total_invoice_amount=1,
            credit_days=30,
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
    line = saved.categories[0]
    assert line.entered_quantity == 2
    assert line.pieces_per_pack == 12
    assert line.base_quantity == 24
    assert line.quantity == 24
    assert line.amount == money(2 * 12 * 10)
    assert saved.total_invoice_amount == money(240)


def test_due_date_follows_credit_days_and_a_manual_date_is_kept(
    repo: ReconciliationRepository,
) -> None:
    credit_days = 30
    automatic = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="DUE-AUTO",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=credit_days),
            total_invoice_amount=1_000,
            credit_days=credit_days,
        )
    )
    assert automatic.due_date == date(2026, 10, 31)
    assert automatic.credit_days == credit_days

    override = date(2026, 12, 15)
    manual = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="DUE-MANUAL",
            supermarket_name="Capital",
            shop="Hyper",
            invoice_date=INVOICE_DATE,
            due_date=override,
            total_invoice_amount=1_000,
            credit_days=credit_days,
        )
    )
    assert manual.due_date == override
    assert manual.due_date != manual.invoice_date + timedelta(days=manual.credit_days)
    assert manual.credit_days == credit_days


@pytest.mark.parametrize("supermarket", ["Capital", "Makro", "One Stop Mart"])
@pytest.mark.parametrize("rate", [0.03, 0.04, 0.05])
def test_discount_is_deducted_and_written_in_the_remark(
    repo: ReconciliationRepository, supermarket: str, rate: float
) -> None:
    total = 100_000.0
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number=f"DISC-{supermarket[:3]}-{int(rate * 100)}",
            supermarket_name=supermarket,
            shop=SHOPS[supermarket],
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=30),
            total_invoice_amount=total,
            discount_percentage=rate,
            credit_days=30,
            tax_percentage=0.05 if supermarket == "Makro" else 0.0,
            retro_percentage=0.05 if supermarket == "Makro" else 0.0,
            categories=[
                InvoiceCategory(category="Snacks", quantity=1, amount=total * 0.6),
                InvoiceCategory(
                    category="Dairy",
                    quantity=1,
                    amount=total * 0.4,
                    tax_exempt=True,
                ),
            ],
        )
    )
    assert saved.discount_percentage == rate
    assert saved.remark == f"Discount {rate * 100:.0f}%: {money(total * rate):,.2f}"
    assert saved.categories[1].tax_exempt is True
    net = total * (1 - rate)
    if supermarket == "Capital":
        expected = net * 0.97
    elif supermarket == "Makro":
        taxable_net = net * 0.6
        exempt_net = net * 0.4
        expected = (taxable_net * 1.05 + exempt_net) * 0.95
    else:
        expected = net
    row = repo.report_rows(supermarket, SHOPS[supermarket])[0]
    assert row["Expected Final Payment"] == money(expected)


def test_a_discount_outside_three_four_or_five_percent_is_rejected(
    repo: ReconciliationRepository,
) -> None:
    with pytest.raises(ValueError, match="Discount must be none, 3%, 4%, or 5%"):
        repo.create_invoice(
            SupermarketInvoice(
                invoice_number="DISC-BAD",
                supermarket_name="Capital",
                shop="Hyper",
                invoice_date=INVOICE_DATE,
                due_date=INVOICE_DATE + timedelta(days=30),
                total_invoice_amount=1_000,
                discount_percentage=0.10,
            )
        )


def test_saved_invoice_keeps_its_price_after_the_product_changes(
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
            invoice_number="SNAP-1",
            supermarket_name="Makro",
            shop="Makro-Sat Sun",
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=30),
            total_invoice_amount=1,
            tax_percentage=0.05,
            retro_percentage=0.05,
            credit_days=30,
            categories=[
                InvoiceCategory(
                    category=chips.category,
                    subcategory=chips.subcategory,
                    product_name=chips.name,
                    unit="Pack",
                    entered_quantity=2,
                    pieces_per_pack=12,
                    price_per_piece=10,
                )
            ],
        )
    )
    assert saved.total_invoice_amount == money(240)
    chips.price_per_piece = 99
    chips.pieces_per_pack = 24
    repo.save_product(chips)
    stored = repo.get_invoice(saved.id or 0)
    assert stored is not None
    assert stored.categories[0].price_per_piece == 10
    assert stored.categories[0].pieces_per_pack == 12
    assert stored.categories[0].base_quantity == 24
    assert stored.total_invoice_amount == money(240)
    assert repo.list_products()[0].price_per_piece == 99
    assert repo.list_products()[0].pieces_per_pack == 24


def test_amount_groups_by_category_and_quantity_stays_on_each_item(
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
    milk = repo.save_product(
        Product(
            category="Dairy",
            subcategory="Milk",
            name="Milk",
            pieces_per_pack=12,
            price_per_piece=5,
        )
    )
    repo.create_invoice(
        SupermarketInvoice(
            invoice_number="SUM-1",
            supermarket_name="One Stop Mart",
            shop="One Stop Mart",
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=30),
            total_invoice_amount=1,
            credit_days=30,
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
                    entered_quantity=3,
                    pieces_per_pack=chocolate.pieces_per_pack,
                    price_per_piece=chocolate.price_per_piece,
                ),
                InvoiceCategory(
                    category=milk.category,
                    subcategory=milk.subcategory,
                    product_name=milk.name,
                    unit="Piece",
                    entered_quantity=4,
                    pieces_per_pack=milk.pieces_per_pack,
                    price_per_piece=milk.price_per_piece,
                    tax_exempt=True,
                ),
            ],
        )
    )
    amounts, missing = repo.city_category_summary("One Stop Mart", "One Stop Mart")
    assert missing == 0
    rolled: dict[str, float] = {}
    for row in amounts:
        assert "Quantity" not in row
        rolled[str(row["Category"])] = money(
            rolled.get(str(row["Category"]), 0) + float(row["Total amount"])
        )
    assert rolled == {"Dairy": money(20), "Snacks": money(240 + 60)}
    items = {
        str(row["Product"]): row
        for row in repo.city_item_summary("One Stop Mart", "One Stop Mart")
    }
    assert set(items) == {"Chocolate", "Milk", "Potato chips"}
    assert items["Potato chips"]["Quantity (pieces)"] == 24
    assert items["Chocolate"]["Quantity (pieces)"] == 3
    assert items["Milk"]["Quantity (pieces)"] == 4
    assert items["Potato chips"]["Category"] == items["Chocolate"]["Category"]


def test_one_stop_mart_payment_uses_the_rates_entered_on_the_payment(
    repo: ReconciliationRepository,
) -> None:
    saved = repo.create_invoice(
        SupermarketInvoice(
            invoice_number="OSM-PAY",
            supermarket_name="One Stop Mart",
            shop="One Stop Mart",
            invoice_date=INVOICE_DATE,
            due_date=INVOICE_DATE + timedelta(days=30),
            total_invoice_amount=80_000,
            credit_days=30,
            categories=[
                InvoiceCategory(category="Snacks", quantity=1, amount=60_000),
                InvoiceCategory(
                    category="Dairy",
                    quantity=1,
                    amount=20_000,
                    tax_exempt=True,
                ),
            ],
        )
    )
    repo.create_payment(
        SupermarketPayment(
            invoice_id=saved.id or 0,
            payment_date=date(2026, 10, 20),
            actual_received_amount=1,
            retro_percentage=0.07,
            delivery_charge_percentage=0.04,
        )
    )
    row = repo.report_rows("One Stop Mart", "One Stop Mart")[0]
    assert row["Expected Final Payment"] == money(80_000 * (1 - 0.07 - 0.04))
