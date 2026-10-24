"""Load Phase 2 sample products and one invoice for each supermarket.

The cases follow phase_2_brd.md. Discount stays at none, so each payment
matches that document's formula. Milk is marked tax exempt on the invoice
line. The product list itself has no tax flag.

Supermarket tax and discount rules live in the application, not in a
settings table. This script uses those rules:

* Capital prices already include 5% tax. Retro and delivery are recorded
  on the payment. G&G delivery is 4%.
* Makro adds 5% tax to the taxable share, then takes 5% retro off the
  grand total. The payment stores the received date and the retro cut-off.
* One Stop Mart adds no tax. The payment takes off back margin plus
  delivery incentive. A tax-exempt line does not change that payment.

Run from the project root:

    .venv/bin/python scripts/seed_phase2.py

An invoice number that already exists is skipped. Other invoices stay.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bnadataentry.db import DEFAULT_DB_PATH, init_db
from bnadataentry.expected_payment import (
    CAPITAL,
    MAKRO,
    ONE_STOP_MART,
    PaymentTerms,
    expected_payment,
)
from bnadataentry.reconciliation import (
    UNIT_PACK,
    UNIT_PIECE,
    InvoiceCategory,
    Product,
    ReconciliationRepository,
    SupermarketInvoice,
    SupermarketPayment,
    capital_delivery_rate,
    exempt_amount,
)

INVOICE_DATE = date(2026, 10, 1)
DUE_DATE = date(2026, 10, 31)
PAYMENT_DATE = date(2026, 10, 20)
CUT_OFF_DATE = date(2026, 10, 15)


def _line(
    product: Product,
    *,
    unit: str,
    entered: float,
    tax_exempt: bool = False,
) -> InvoiceCategory:
    return InvoiceCategory(
        category=product.category,
        subcategory=product.subcategory,
        product_name=product.name,
        unit=unit,
        entered_quantity=entered,
        pieces_per_pack=product.pieces_per_pack,
        price_per_piece=product.price_per_piece,
        tax_exempt=tax_exempt,
    )


def _ensure_product(repo: ReconciliationRepository, product: Product) -> Product:
    for existing in repo.list_products():
        if (
            existing.category == product.category
            and existing.subcategory == product.subcategory
            and existing.name == product.name
        ):
            print(f"  product already present: {product.category} / {product.name}")
            return existing
    saved = repo.save_product(product)
    print(
        f"  added {saved.category} / {saved.subcategory} / {saved.name}: "
        f"1 pack = {saved.pieces_per_pack:g} pieces, "
        f"{saved.price_per_piece:,.2f} per piece"
    )
    return saved


def _invoice_exists(repo: ReconciliationRepository, number: str) -> bool:
    return any(item.invoice_number == number for item in repo.list_invoices())


def _save_case(
    repo: ReconciliationRepository,
    invoice: SupermarketInvoice,
    *,
    retro: float,
    delivery: float = 0.0,
    tax: float = 0.0,
    cut_off: date | None = None,
) -> None:
    if _invoice_exists(repo, invoice.invoice_number):
        print(f"  invoice {invoice.invoice_number} already present, left unchanged")
        return
    saved = repo.create_invoice(invoice)
    assert saved.id is not None
    terms = PaymentTerms(
        total_invoice_amount=saved.total_invoice_amount,
        is_tax_included=saved.is_tax_included,
        tax_treatment=saved.tax_treatment,
        retro_percentage=retro,
        delivery_charge_percentage=delivery,
        tax_percentage=tax,
        discount_percentage=saved.discount_percentage,
        exempt_amount=exempt_amount(saved),
    )
    expected = expected_payment(saved.supermarket_name, terms).expected_final_payment
    repo.create_payment(
        SupermarketPayment(
            invoice_id=saved.id,
            payment_date=PAYMENT_DATE,
            actual_received_amount=expected,
            retro_percentage=retro,
            delivery_charge_percentage=delivery,
            tax_percentage=tax,
            retro_cut_off_date=cut_off,
        )
    )
    loaded = repo.get_invoice(saved.id)
    assert loaded is not None
    payments = repo.list_payments(saved.id)
    result = expected_payment(loaded.supermarket_name, repo.terms_for(loaded, payments))
    actual = payments[0].actual_received_amount
    print(f"  {loaded.invoice_number} · {loaded.supermarket_name} · {loaded.shop}")
    print(f"    invoice total {loaded.total_invoice_amount:,.2f}")
    for label, value in result.steps:
        print(f"    {label}: {value}")
    print(f"    received {actual:,.2f} on {payments[0].payment_date.isoformat()}")
    if payments[0].retro_cut_off_date is not None:
        print(f"    retro cut-off {payments[0].retro_cut_off_date.isoformat()}")
    print(f"    status {loaded.status}")
    print("    lines:")
    for line in loaded.categories:
        exempt = "tax exempt" if line.tax_exempt else "taxable"
        print(
            f"      {line.product_name}: {line.entered_quantity:g} {line.unit}, "
            f"{line.base_quantity:g} pieces, {line.amount:,.2f} ({exempt})"
        )


def seed(repo: ReconciliationRepository) -> None:
    print("Products")
    chips = _ensure_product(
        repo,
        Product(
            category="Snacks",
            subcategory="Chips",
            name="Potato chips",
            pieces_per_pack=12,
            price_per_piece=1000,
        ),
    )
    _ensure_product(
        repo,
        Product(
            category="Snacks",
            subcategory="Chocolate",
            name="Chocolate",
            pieces_per_pack=6,
            price_per_piece=1500,
        ),
    )
    milk = _ensure_product(
        repo,
        Product(
            category="Dairy",
            subcategory="Milk",
            name="Milk",
            pieces_per_pack=12,
            price_per_piece=800,
        ),
    )

    print("Supermarket rules used by these invoices")
    print("  Capital: tax included in the price. Retro 3% and G&G delivery 4%.")
    print("  Makro: tax 5% on the taxable share, then retro 5%.")
    print("  One Stop Mart: back margin 5% + delivery incentive 5%, no added tax.")

    print("Invoices")
    _save_case(
        repo,
        SupermarketInvoice(
            invoice_number="P2-CAP-001",
            supermarket_name=CAPITAL,
            shop="G&G",
            invoice_date=INVOICE_DATE,
            due_date=DUE_DATE,
            total_invoice_amount=0,
            credit_days=30,
            categories=[
                _line(chips, unit=UNIT_PACK, entered=8),
                _line(milk, unit=UNIT_PIECE, entered=5, tax_exempt=True),
            ],
        ),
        retro=0.03,
        delivery=capital_delivery_rate("G&G"),
    )
    _save_case(
        repo,
        SupermarketInvoice(
            invoice_number="P2-MAK-001",
            supermarket_name=MAKRO,
            shop="Makro-Sat Sun",
            invoice_date=INVOICE_DATE,
            due_date=DUE_DATE,
            total_invoice_amount=0,
            tax_percentage=0.05,
            retro_percentage=0.05,
            credit_days=30,
            categories=[
                _line(chips, unit=UNIT_PACK, entered=10),
                _line(milk, unit=UNIT_PIECE, entered=10, tax_exempt=True),
            ],
        ),
        retro=0.05,
        tax=0.05,
        cut_off=CUT_OFF_DATE,
    )
    _save_case(
        repo,
        SupermarketInvoice(
            invoice_number="P2-OSM-001",
            supermarket_name=ONE_STOP_MART,
            shop="One Stop Mart",
            invoice_date=INVOICE_DATE,
            due_date=DUE_DATE,
            total_invoice_amount=0,
            credit_days=30,
            categories=[
                _line(chips, unit=UNIT_PACK, entered=4),
                _line(milk, unit=UNIT_PIECE, entered=10, tax_exempt=True),
            ],
        ),
        retro=0.05,
        delivery=0.05,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite file to fill (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    conn = init_db(args.db)
    try:
        seed(ReconciliationRepository(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
