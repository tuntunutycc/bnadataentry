"""Invoice and payment records for supermarket reconciliation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from bnadataentry.expected_payment import (
    CAPITAL,
    MAKRO,
    NO_TAX_NO_RETRO,
    ONE_STOP_MART,
    SUPERMARKETS,
    TAX_EXCLUSIVE,
    TAX_INCLUDED,
    PaymentTerms,
    expected_payment,
    money,
)

# Shop names are the detail-sheet names in excel_file/.
SHOPS: dict[str, tuple[str, ...]] = {
    CAPITAL: (
        "CREL Distribution Center",
        "DC MDY",
        "G&G",
        "Hyper",
    ),
    MAKRO: (
        "Makro-Sat Sun",
        "Makro - Kha Wei Chan",
    ),
    ONE_STOP_MART: ("One Stop Mart",),
}

UNIT_PIECE = "Piece"
UNIT_PACK = "Pack"
UNITS = (UNIT_PIECE, UNIT_PACK)
ALLOWED_DISCOUNTS = (0.0, 0.03, 0.04, 0.05)

STATUS_PENDING = "Pending"
STATUS_PARTIAL = "Partial"
STATUS_PAID = "Paid"
STATUSES = (STATUS_PENDING, STATUS_PARTIAL, STATUS_PAID)

_PAID_TOLERANCE = 0.01


def shops_for(supermarket_name: str) -> tuple[str, ...]:
    """Shops for one supermarket, taken from its Excel workbook sheets."""
    return SHOPS.get(supermarket_name, ())


def capital_delivery_rate(shop: str) -> float:
    """Capital delivery charge: Hyper 0%, DC MDY 6%, CREL and G&G 4%."""
    name = shop.strip()
    if name == "Hyper" or name.startswith("Hyper"):
        return 0.0
    if name == "DC MDY":
        return 0.06
    return 0.04


def payment_status(expected: float, actual_received: float) -> str:
    """Pending when nothing has arrived, Partial when short, Paid when met or over."""
    actual = money(actual_received)
    due = money(expected)
    if actual <= _PAID_TOLERANCE:
        return STATUS_PENDING
    if actual + _PAID_TOLERANCE < due:
        return STATUS_PARTIAL
    return STATUS_PAID


@dataclass
class InvoiceCategory:
    """One category line, or one Capital product line, on an invoice."""

    category: str
    quantity: float = 0.0
    amount: float = 0.0
    subcategory: str = ""
    product_name: str = ""
    unit: str = ""
    entered_quantity: float = 0.0
    pieces_per_pack: float = 0.0
    price_per_piece: float = 0.0
    base_quantity: float = 0.0
    tax_exempt: bool = False


@dataclass
class Product:
    """Capital master product. Invoice lines snapshot these values."""

    category: str
    subcategory: str
    name: str
    pieces_per_pack: float
    price_per_piece: float
    id: int | None = None
    is_active: bool = True


@dataclass
class SupermarketInvoice:
    invoice_number: str
    supermarket_name: str
    invoice_date: date
    due_date: date
    total_invoice_amount: float
    shop: str = ""
    is_tax_included: bool = False
    tax_treatment: str = ""
    tax_percentage: float = 0.0
    retro_percentage: float = 0.0
    discount_percentage: float = 0.0
    remark: str = ""
    credit_days: int = 30
    categories: list[InvoiceCategory] = field(default_factory=list)
    status: str = STATUS_PENDING
    id: int | None = None


@dataclass
class SupermarketPayment:
    invoice_id: int
    payment_date: date
    actual_received_amount: float
    retro_percentage: float = 0.0
    delivery_charge_percentage: float = 0.0
    tax_percentage: float = 0.0
    retro_cut_off_date: date | None = None
    id: int | None = None


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_to_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _validate_invoice(invoice: SupermarketInvoice) -> None:
    number = invoice.invoice_number.strip()
    if not number:
        raise ValueError("Invoice number is required.")
    if invoice.supermarket_name not in SUPERMARKETS:
        raise ValueError(f"Unknown supermarket: {invoice.supermarket_name}")
    shops = shops_for(invoice.supermarket_name)
    if invoice.shop not in shops:
        raise ValueError("Choose a shop for this supermarket.")
    if invoice.due_date < invoice.invoice_date:
        raise ValueError("Due date cannot be before the invoice date.")
    if int(invoice.credit_days) < 0:
        raise ValueError("Credit days cannot be negative.")
    invoice.credit_days = int(invoice.credit_days)
    _normalize_tax_treatment(invoice)
    _normalize_categories(invoice)
    _normalize_discount(invoice)
    if invoice.total_invoice_amount <= 0:
        raise ValueError("Invoice amount must be greater than zero.")


def discount_note(total: float, rate: float) -> str:
    """Remark text for the chosen discount. Empty when no discount applies."""
    if rate <= 0:
        return ""
    return f"Discount {rate * 100:.0f}%: {money(total * rate):,.2f}"


def exempt_amount(invoice: SupermarketInvoice) -> float:
    """Sum of Capital line amounts marked tax exempt."""
    return money(sum(line.amount for line in invoice.categories if line.tax_exempt))


def _normalize_discount(invoice: SupermarketInvoice) -> None:
    rate = float(invoice.discount_percentage)
    chosen = next(
        (item for item in ALLOWED_DISCOUNTS if abs(rate - item) < 1e-9),
        None,
    )
    if chosen is None:
        raise ValueError("Discount must be none, 3%, 4%, or 5%.")
    invoice.discount_percentage = chosen
    invoice.remark = invoice.remark.strip()
    if chosen and not invoice.remark:
        invoice.remark = discount_note(invoice.total_invoice_amount, chosen)


def _blank_city_line(line: InvoiceCategory) -> bool:
    return (
        not str(line.category).strip()
        and not str(line.subcategory).strip()
        and not str(line.product_name).strip()
        and float(line.entered_quantity or 0) == 0
    )


def _city_mart_line(line: InvoiceCategory) -> InvoiceCategory:
    category = str(line.category).strip()
    subcategory = str(line.subcategory).strip()
    product_name = str(line.product_name).strip()
    if not category or not subcategory or not product_name:
        raise ValueError("Choose a category, sub-category, and product.")
    unit = str(line.unit).strip()
    if unit not in UNITS:
        raise ValueError("Choose Piece or Pack.")
    entered = float(line.entered_quantity or 0)
    if entered <= 0:
        raise ValueError("Quantity must be greater than zero.")
    pieces = float(line.pieces_per_pack or 0)
    price = float(line.price_per_piece or 0)
    if pieces <= 0:
        raise ValueError("Pieces per pack must be greater than zero.")
    if price < 0:
        raise ValueError("Price cannot be negative.")
    unit_price = price if unit == UNIT_PIECE else price * pieces
    base = entered if unit == UNIT_PIECE else entered * pieces
    amount = money(entered * unit_price)
    return InvoiceCategory(
        category=category,
        subcategory=subcategory,
        product_name=product_name,
        quantity=base,
        amount=amount,
        unit=unit,
        entered_quantity=entered,
        pieces_per_pack=pieces,
        price_per_piece=price,
        base_quantity=base,
        tax_exempt=bool(line.tax_exempt),
    )


def _normalize_city_mart_lines(invoice: SupermarketInvoice) -> None:
    kept = [
        _city_mart_line(line)
        for line in invoice.categories
        if not _blank_city_line(line)
    ]
    if not kept:
        raise ValueError("Add at least one product.")
    invoice.categories = kept
    invoice.total_invoice_amount = money(sum(line.amount for line in kept))


def _uses_product_lines(invoice: SupermarketInvoice) -> bool:
    """Lines chosen from the product list, not a typed category name."""
    return any(
        str(line.product_name).strip() or str(line.unit).strip()
        for line in invoice.categories
    )


def _normalize_categories(invoice: SupermarketInvoice) -> None:
    """Keep named category lines and combine repeated names on one invoice."""
    if _uses_product_lines(invoice):
        _normalize_city_mart_lines(invoice)
        return
    merged: dict[tuple[str, bool], InvoiceCategory] = {}
    order: list[tuple[str, bool]] = []
    for line in invoice.categories:
        name = str(line.category).strip()
        quantity = float(line.quantity or 0)
        amount = float(line.amount or 0)
        exempt = bool(line.tax_exempt)
        if not name and quantity == 0 and amount == 0:
            continue
        if not name:
            raise ValueError("Each category line needs a name.")
        if quantity < 0:
            raise ValueError("Quantity cannot be negative.")
        if amount < 0:
            raise ValueError("Category amount cannot be negative.")
        key = (name, exempt)
        if key not in merged:
            merged[key] = InvoiceCategory(category=name, tax_exempt=exempt)
            order.append(key)
        current = merged[key]
        current.quantity += quantity
        current.amount = money(current.amount + amount)
    if len(order) == 1 and merged[order[0]].amount == 0:
        merged[order[0]].amount = money(invoice.total_invoice_amount)
    invoice.categories = [merged[key] for key in order]


def _normalize_tax_treatment(invoice: SupermarketInvoice) -> None:
    """Keep the tax choice and the Capital tax flag in agreement."""
    if invoice.supermarket_name == CAPITAL:
        invoice.tax_treatment = TAX_INCLUDED
    elif invoice.supermarket_name == MAKRO:
        _validate_rate("Tax", invoice.tax_percentage)
        _validate_rate("Retro", invoice.retro_percentage)
        if invoice.tax_percentage > 0:
            invoice.tax_treatment = TAX_INCLUDED
        elif invoice.retro_percentage > 0:
            invoice.tax_treatment = TAX_EXCLUSIVE
        else:
            invoice.tax_treatment = NO_TAX_NO_RETRO
    else:
        invoice.tax_treatment = TAX_EXCLUSIVE
        invoice.tax_percentage = 0.0
        invoice.retro_percentage = 0.0
    invoice.is_tax_included = invoice.tax_treatment == TAX_INCLUDED


def _validate_rate(name: str, rate: float) -> None:
    if rate < 0 or rate > 1:
        raise ValueError(f"{name} must be between 0% and 100%.")


def default_terms(invoice: SupermarketInvoice) -> PaymentTerms:
    """Rates used before a payment is recorded.

    Capital delivery is applied only when a payment is saved. Makro uses the
    tax and retro percents saved on the invoice.
    """
    if invoice.supermarket_name == CAPITAL:
        return PaymentTerms(
            total_invoice_amount=invoice.total_invoice_amount,
            is_tax_included=True,
            tax_treatment=TAX_INCLUDED,
            retro_percentage=0.03,
            delivery_charge_percentage=0.0,
            discount_percentage=invoice.discount_percentage,
            exempt_amount=exempt_amount(invoice),
        )
    if invoice.supermarket_name == MAKRO:
        return PaymentTerms(
            total_invoice_amount=invoice.total_invoice_amount,
            tax_percentage=invoice.tax_percentage,
            retro_percentage=invoice.retro_percentage,
            discount_percentage=invoice.discount_percentage,
            exempt_amount=exempt_amount(invoice),
        )
    return PaymentTerms(
        total_invoice_amount=invoice.total_invoice_amount,
        discount_percentage=invoice.discount_percentage,
        exempt_amount=exempt_amount(invoice),
    )


class ReconciliationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_invoice(self, invoice: SupermarketInvoice) -> SupermarketInvoice:
        _validate_invoice(invoice)
        invoice.invoice_number = invoice.invoice_number.strip()
        invoice.status = STATUS_PENDING
        try:
            cur = self.conn.execute(
                """
                INSERT INTO supermarket_invoices (
                    invoice_number, supermarket_name, shop, invoice_date, due_date,
                    total_invoice_amount, is_tax_included, tax_treatment,
                    tax_percentage, retro_percentage, discount_percentage, remark,
                    credit_days, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice.invoice_number,
                    invoice.supermarket_name,
                    invoice.shop,
                    invoice.invoice_date.isoformat(),
                    invoice.due_date.isoformat(),
                    money(invoice.total_invoice_amount),
                    1 if invoice.is_tax_included else 0,
                    invoice.tax_treatment,
                    float(invoice.tax_percentage),
                    float(invoice.retro_percentage),
                    float(invoice.discount_percentage),
                    invoice.remark,
                    int(invoice.credit_days),
                    invoice.status,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Invoice {invoice.invoice_number} already exists."
            ) from exc
        invoice.id = int(cur.lastrowid)
        self._replace_categories(invoice.id, invoice.categories)
        self.conn.commit()
        invoice.total_invoice_amount = money(invoice.total_invoice_amount)
        return invoice

    def update_invoice(self, invoice: SupermarketInvoice) -> SupermarketInvoice:
        """Replace the editable fields of one saved invoice."""
        if invoice.id is None or self.get_invoice(invoice.id) is None:
            raise ValueError("Invoice not found.")
        _validate_invoice(invoice)
        invoice.invoice_number = invoice.invoice_number.strip()
        invoice.total_invoice_amount = money(invoice.total_invoice_amount)
        try:
            self.conn.execute(
                """
                UPDATE supermarket_invoices
                SET invoice_number = ?, invoice_date = ?, due_date = ?,
                    total_invoice_amount = ?, is_tax_included = ?, tax_treatment = ?,
                    tax_percentage = ?, retro_percentage = ?,
                    discount_percentage = ?, remark = ?, credit_days = ?,
                    category = '', quantity = 0
                WHERE id = ?
                """,
                (
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat(),
                    invoice.due_date.isoformat(),
                    invoice.total_invoice_amount,
                    1 if invoice.is_tax_included else 0,
                    invoice.tax_treatment,
                    float(invoice.tax_percentage),
                    float(invoice.retro_percentage),
                    float(invoice.discount_percentage),
                    invoice.remark,
                    int(invoice.credit_days),
                    invoice.id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Invoice {invoice.invoice_number} already exists."
            ) from exc
        self._replace_categories(invoice.id, invoice.categories)
        self._refresh_status(invoice)
        self.conn.commit()
        return invoice

    def list_invoices(
        self,
        supermarket_name: str | None = None,
        shop: str | None = None,
    ) -> list[SupermarketInvoice]:
        clauses: list[str] = []
        params: list[str] = []
        if supermarket_name:
            clauses.append("supermarket_name = ?")
            params.append(supermarket_name)
        if shop:
            clauses.append("shop = ?")
            params.append(shop)
        sql = "SELECT * FROM supermarket_invoices"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY invoice_date DESC, invoice_number"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._invoice_from_row(row) for row in rows]

    def get_invoice(self, invoice_id: int) -> SupermarketInvoice | None:
        row = self.conn.execute(
            "SELECT * FROM supermarket_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        if row is None:
            return None
        return self._invoice_from_row(row)

    def delete_invoice(self, invoice_id: int) -> None:
        """Remove one invoice together with its payments and category lines."""
        self.conn.execute(
            "DELETE FROM invoice_categories WHERE invoice_id = ?",
            (invoice_id,),
        )
        self.conn.execute(
            "DELETE FROM supermarket_payments WHERE invoice_id = ?",
            (invoice_id,),
        )
        self.conn.execute(
            "DELETE FROM supermarket_invoices WHERE id = ?", (invoice_id,)
        )
        self.conn.commit()

    def clear_invoices_and_payments(self) -> tuple[int, int]:
        """Delete every reconciliation invoice and its payments."""
        payment_count = int(
            self.conn.execute("SELECT COUNT(*) FROM supermarket_payments").fetchone()[0]
        )
        invoice_count = int(
            self.conn.execute("SELECT COUNT(*) FROM supermarket_invoices").fetchone()[0]
        )
        self.conn.execute("DELETE FROM invoice_categories")
        self.conn.execute("DELETE FROM supermarket_payments")
        self.conn.execute("DELETE FROM supermarket_invoices")
        self.conn.commit()
        return invoice_count, payment_count

    def clear_all_data(self) -> tuple[int, int, int]:
        """Delete invoices, payments, and the product list so entry can start over."""
        invoices, payments = self.clear_invoices_and_payments()
        product_count = int(
            self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        )
        self.conn.execute("DELETE FROM products")
        self.conn.execute("DELETE FROM product_subcategories")
        self.conn.execute("DELETE FROM product_categories")
        self.conn.commit()
        return invoices, payments, product_count

    def create_payment(self, payment: SupermarketPayment) -> SupermarketPayment:
        invoice = self.get_invoice(payment.invoice_id)
        if invoice is None:
            raise ValueError("Invoice not found.")
        if payment.actual_received_amount < 0:
            raise ValueError("Received amount cannot be negative.")
        _validate_rate("Tax", payment.tax_percentage)
        _validate_rate("Retro", payment.retro_percentage)
        _validate_rate("Delivery charge", payment.delivery_charge_percentage)
        if invoice.supermarket_name == MAKRO:
            invoice.tax_percentage = float(payment.tax_percentage)
            invoice.retro_percentage = float(payment.retro_percentage)
            _normalize_tax_treatment(invoice)
            self.conn.execute(
                """
                UPDATE supermarket_invoices
                SET tax_percentage = ?, retro_percentage = ?,
                    tax_treatment = ?, is_tax_included = ?
                WHERE id = ?
                """,
                (
                    invoice.tax_percentage,
                    invoice.retro_percentage,
                    invoice.tax_treatment,
                    1 if invoice.is_tax_included else 0,
                    invoice.id,
                ),
            )
        cur = self.conn.execute(
            """
            INSERT INTO supermarket_payments (
                invoice_id, payment_date, actual_received_amount,
                retro_percentage, delivery_charge_percentage, tax_percentage,
                retro_cut_off_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment.invoice_id,
                payment.payment_date.isoformat(),
                money(payment.actual_received_amount),
                float(payment.retro_percentage),
                float(payment.delivery_charge_percentage),
                float(payment.tax_percentage),
                _date_to_str(payment.retro_cut_off_date),
            ),
        )
        payment.id = int(cur.lastrowid)
        payment.actual_received_amount = money(payment.actual_received_amount)
        self._refresh_status(invoice)
        self.conn.commit()
        return payment

    def list_payments(self, invoice_id: int) -> list[SupermarketPayment]:
        rows = self.conn.execute(
            """
            SELECT * FROM supermarket_payments
            WHERE invoice_id = ?
            ORDER BY payment_date, id
            """,
            (invoice_id,),
        ).fetchall()
        return [self._payment_from_row(row) for row in rows]

    def delete_payment(self, payment_id: int) -> None:
        row = self.conn.execute(
            "SELECT invoice_id FROM supermarket_payments WHERE id = ?",
            (payment_id,),
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "DELETE FROM supermarket_payments WHERE id = ?", (payment_id,)
        )
        invoice = self.get_invoice(int(row["invoice_id"]))
        if invoice is not None:
            self._refresh_status(invoice)
        self.conn.commit()

    def report_rows(
        self,
        supermarket_name: str | None = None,
        shop: str | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for invoice in self.list_invoices(supermarket_name, shop):
            assert invoice.id is not None
            payments = self.list_payments(invoice.id)
            terms = self.terms_for(invoice, payments)
            expected = expected_payment(invoice.supermarket_name, terms)
            actual = money(sum(p.actual_received_amount for p in payments))
            variance = money(actual - expected.expected_final_payment)
            latest = (
                max(payments, key=lambda p: (p.payment_date, p.id or 0))
                if payments
                else None
            )
            rows.append(
                {
                    "id": invoice.id,
                    "Invoice No.": invoice.invoice_number,
                    "Supermarket": invoice.supermarket_name,
                    "Shop": invoice.shop,
                    "Invoice Date": invoice.invoice_date.isoformat(),
                    "Due Date": invoice.due_date.isoformat(),
                    "Total Invoice Amount": money(invoice.total_invoice_amount),
                    "Expected Final Payment": expected.expected_final_payment,
                    "Actual Received Amount": actual,
                    "Variance": variance,
                    "Status": payment_status(
                        expected.expected_final_payment, actual
                    ),
                    "Tax %": round(invoice.tax_percentage * 100, 2),
                    "Retro %": round(invoice.retro_percentage * 100, 2),
                    "Received Date": (
                        latest.payment_date.isoformat() if latest else ""
                    ),
                    "Retro cut-off": (
                        latest.retro_cut_off_date.isoformat()
                        if latest and latest.retro_cut_off_date
                        else ""
                    ),
                    "steps": expected.steps,
                }
            )
        return rows

    def category_summary(
        self, supermarket_name: str, shop: str
    ) -> tuple[list[dict[str, object]], int]:
        """Quantity and category amount for the selected shop.

        An invoice can contribute to more than one category. The amount is the
        sum of the category lines, so one invoice total is not added twice.
        """
        grouped = self.conn.execute(
            """
            SELECT TRIM(c.category) AS category,
                   SUM(c.quantity) AS quantity,
                   SUM(c.amount) AS amount,
                   COUNT(DISTINCT c.invoice_id) AS invoices
            FROM invoice_categories AS c
            JOIN supermarket_invoices AS i ON i.id = c.invoice_id
            WHERE i.supermarket_name = ? AND i.shop = ? AND TRIM(c.category) != ''
            GROUP BY TRIM(c.category)
            ORDER BY TRIM(c.category)
            """,
            (supermarket_name, shop),
        ).fetchall()
        missing = self.conn.execute(
            """
            SELECT COUNT(*) FROM supermarket_invoices AS i
            WHERE i.supermarket_name = ? AND i.shop = ?
              AND NOT EXISTS (
                  SELECT 1 FROM invoice_categories AS c
                  WHERE c.invoice_id = i.id AND TRIM(c.category) != ''
              )
            """,
            (supermarket_name, shop),
        ).fetchone()
        rows = [
            {
                "Category": row["category"],
                "Quantity": float(row["quantity"]),
                "Total amount": money(float(row["amount"])),
                "Invoices": int(row["invoices"]),
            }
            for row in grouped
        ]
        return rows, int(missing[0] if missing is not None else 0)

    def city_category_summary(
        self, supermarket_name: str, shop: str
    ) -> tuple[list[dict[str, object]], int]:
        """Amounts grouped by category and sub-category."""
        grouped = self.conn.execute(
            """
            SELECT TRIM(c.category) AS category,
                   TRIM(c.subcategory) AS subcategory,
                   SUM(c.amount) AS amount
            FROM invoice_categories AS c
            JOIN supermarket_invoices AS i ON i.id = c.invoice_id
            WHERE i.supermarket_name = ? AND i.shop = ?
              AND TRIM(c.category) != ''
            GROUP BY TRIM(c.category), TRIM(c.subcategory)
            ORDER BY TRIM(c.category), TRIM(c.subcategory)
            """,
            (supermarket_name, shop),
        ).fetchall()
        missing = self._invoices_without_lines(supermarket_name, shop)
        rows = [
            {
                "Category": row["category"],
                "Sub-category": row["subcategory"],
                "Total amount": money(float(row["amount"])),
            }
            for row in grouped
        ]
        return rows, missing

    def city_item_summary(
        self, supermarket_name: str, shop: str
    ) -> list[dict[str, object]]:
        """Piece quantities grouped by product, not added across products."""
        grouped = self.conn.execute(
            """
            SELECT TRIM(c.product_name) AS product,
                   TRIM(c.category) AS category,
                   TRIM(c.subcategory) AS subcategory,
                   SUM(c.base_quantity) AS pieces,
                   SUM(c.amount) AS amount
            FROM invoice_categories AS c
            JOIN supermarket_invoices AS i ON i.id = c.invoice_id
            WHERE i.supermarket_name = ? AND i.shop = ?
              AND TRIM(c.product_name) != ''
            GROUP BY TRIM(c.product_name), TRIM(c.category), TRIM(c.subcategory)
            ORDER BY TRIM(c.category), TRIM(c.product_name)
            """,
            (supermarket_name, shop),
        ).fetchall()
        return [
            {
                "Product": row["product"],
                "Category": row["category"],
                "Sub-category": row["subcategory"],
                "Quantity (pieces)": float(row["pieces"]),
                "Amount": money(float(row["amount"])),
            }
            for row in grouped
        ]

    def _invoices_without_lines(self, supermarket_name: str, shop: str) -> int:
        missing = self.conn.execute(
            """
            SELECT COUNT(*) FROM supermarket_invoices AS i
            WHERE i.supermarket_name = ? AND i.shop = ?
              AND NOT EXISTS (
                  SELECT 1 FROM invoice_categories AS c
                  WHERE c.invoice_id = i.id AND TRIM(c.category) != ''
              )
            """,
            (supermarket_name, shop),
        ).fetchone()
        return int(missing[0] if missing is not None else 0)

    def terms_for(
        self,
        invoice: SupermarketInvoice,
        payments: list[SupermarketPayment] | None = None,
    ) -> PaymentTerms:
        """Latest payment supplies the variable rates; otherwise use defaults."""
        if payments is None and invoice.id is not None:
            payments = self.list_payments(invoice.id)
        if not payments or invoice.supermarket_name == MAKRO:
            return default_terms(invoice)
        latest = max(payments, key=lambda p: (p.payment_date, p.id or 0))
        if invoice.supermarket_name == CAPITAL:
            return PaymentTerms(
                total_invoice_amount=invoice.total_invoice_amount,
                is_tax_included=True,
                tax_treatment=TAX_INCLUDED,
                retro_percentage=latest.retro_percentage,
                delivery_charge_percentage=latest.delivery_charge_percentage,
                discount_percentage=invoice.discount_percentage,
                exempt_amount=exempt_amount(invoice),
            )
        return PaymentTerms(
            total_invoice_amount=invoice.total_invoice_amount,
            retro_percentage=latest.retro_percentage,
            delivery_charge_percentage=latest.delivery_charge_percentage,
            discount_percentage=invoice.discount_percentage,
            exempt_amount=exempt_amount(invoice),
        )

    def _refresh_status(self, invoice: SupermarketInvoice) -> None:
        assert invoice.id is not None
        payments = self.list_payments(invoice.id)
        terms = self.terms_for(invoice, payments)
        expected = expected_payment(invoice.supermarket_name, terms)
        actual = sum(p.actual_received_amount for p in payments)
        status = payment_status(expected.expected_final_payment, actual)
        self.conn.execute(
            "UPDATE supermarket_invoices SET status = ? WHERE id = ?",
            (status, invoice.id),
        )
        invoice.status = status

    def _replace_categories(
        self, invoice_id: int, lines: list[InvoiceCategory]
    ) -> None:
        self.conn.execute(
            "DELETE FROM invoice_categories WHERE invoice_id = ?",
            (invoice_id,),
        )
        self.conn.executemany(
            """
            INSERT INTO invoice_categories (
                invoice_id, category, quantity, amount, subcategory, product_name,
                unit, entered_quantity, pieces_per_pack, price_per_piece,
                base_quantity, tax_exempt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    invoice_id,
                    line.category,
                    float(line.quantity),
                    money(line.amount),
                    line.subcategory,
                    line.product_name,
                    line.unit,
                    float(line.entered_quantity),
                    float(line.pieces_per_pack),
                    float(line.price_per_piece),
                    float(line.base_quantity),
                    1 if line.tax_exempt else 0,
                )
                for line in lines
            ],
        )

    def _categories_for(self, invoice_id: int) -> list[InvoiceCategory]:
        rows = self.conn.execute(
            """
            SELECT category, quantity, amount, subcategory, product_name, unit,
                   entered_quantity, pieces_per_pack, price_per_piece,
                   base_quantity, tax_exempt
            FROM invoice_categories
            WHERE invoice_id = ?
            ORDER BY id
            """,
            (invoice_id,),
        ).fetchall()
        return [
            InvoiceCategory(
                category=row["category"],
                quantity=float(row["quantity"]),
                amount=float(row["amount"]),
                subcategory=row["subcategory"],
                product_name=row["product_name"],
                unit=row["unit"],
                entered_quantity=float(row["entered_quantity"]),
                pieces_per_pack=float(row["pieces_per_pack"]),
                price_per_piece=float(row["price_per_piece"]),
                base_quantity=float(row["base_quantity"]),
                tax_exempt=bool(row["tax_exempt"]),
            )
            for row in rows
        ]

    def _invoice_from_row(self, row: sqlite3.Row) -> SupermarketInvoice:
        invoice_id = int(row["id"])
        return SupermarketInvoice(
            id=invoice_id,
            invoice_number=row["invoice_number"],
            supermarket_name=row["supermarket_name"],
            shop=row["shop"],
            invoice_date=date.fromisoformat(row["invoice_date"]),
            due_date=date.fromisoformat(row["due_date"]),
            total_invoice_amount=float(row["total_invoice_amount"]),
            is_tax_included=bool(row["is_tax_included"]),
            tax_treatment=row["tax_treatment"],
            tax_percentage=float(row["tax_percentage"]),
            retro_percentage=float(row["retro_percentage"]),
            discount_percentage=float(row["discount_percentage"]),
            remark=row["remark"],
            credit_days=int(row["credit_days"]),
            categories=self._categories_for(invoice_id),
            status=row["status"],
        )

    def list_products(self, *, active_only: bool = True) -> list[Product]:
        """Products for the invoice dropdown. Inactive rows stay out of that list."""
        where = "WHERE p.is_active = 1" if active_only else ""
        rows = self.conn.execute(
            f"""
            SELECT p.id, c.name AS category, s.name AS subcategory, p.name,
                   p.pieces_per_pack, p.price_per_piece, p.is_active
            FROM products AS p
            JOIN product_subcategories AS s ON s.id = p.subcategory_id
            JOIN product_categories AS c ON c.id = s.category_id
            {where}
            ORDER BY c.name, s.name, p.name
            """
        ).fetchall()
        return [
            Product(
                id=int(row["id"]),
                category=row["category"],
                subcategory=row["subcategory"],
                name=row["name"],
                pieces_per_pack=float(row["pieces_per_pack"]),
                price_per_piece=float(row["price_per_piece"]),
                is_active=bool(row["is_active"]),
            )
            for row in rows
        ]

    def save_product(self, product: Product) -> Product:
        """Create or update a Capital product. Old invoices keep their snapshots."""
        category = product.category.strip()
        subcategory = product.subcategory.strip()
        name = product.name.strip()
        pieces = float(product.pieces_per_pack)
        price = float(product.price_per_piece)
        if not category or not subcategory or not name:
            raise ValueError("Category, sub-category, and product name are required.")
        if pieces <= 0:
            raise ValueError("Pieces per pack must be greater than zero.")
        if price < 0:
            raise ValueError("Price cannot be negative.")
        category_id = self._category_id(category)
        subcategory_id = self._subcategory_id(category_id, subcategory)
        try:
            if product.id is None:
                existing = self.conn.execute(
                    """
                    SELECT id, is_active FROM products
                    WHERE subcategory_id = ? AND name = ?
                    """,
                    (subcategory_id, name),
                ).fetchone()
                if existing is not None and int(existing["is_active"]):
                    raise ValueError(
                        f"Product {name} already exists in {subcategory}."
                    )
                if existing is not None:
                    product_id = int(existing["id"])
                    self.conn.execute(
                        """
                        UPDATE products
                        SET pieces_per_pack = ?, price_per_piece = ?, is_active = 1
                        WHERE id = ?
                        """,
                        (pieces, price, product_id),
                    )
                else:
                    cur = self.conn.execute(
                        """
                        INSERT INTO products (
                            subcategory_id, name, pieces_per_pack, price_per_piece,
                            is_active
                        ) VALUES (?, ?, ?, ?, 1)
                        """,
                        (subcategory_id, name, pieces, price),
                    )
                    product_id = int(cur.lastrowid)
            else:
                self.conn.execute(
                    """
                    UPDATE products
                    SET subcategory_id = ?, name = ?, pieces_per_pack = ?,
                        price_per_piece = ?, is_active = 1
                    WHERE id = ?
                    """,
                    (subcategory_id, name, pieces, price, product.id),
                )
                product_id = product.id
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Product {name} already exists in {subcategory}."
            ) from exc
        self.conn.commit()
        saved = next(item for item in self.list_products() if item.id == product_id)
        return saved

    def delete_product(self, product_id: int) -> None:
        """Hide a product that an invoice already uses. Delete the row otherwise.

        Invoice lines keep their own copy of the name, pack size, and price,
        so a hidden product does not change saved invoices or reports.
        """
        product = next(
            (
                item
                for item in self.list_products(active_only=False)
                if item.id == product_id
            ),
            None,
        )
        if product is None:
            return
        used = self.conn.execute(
            """
            SELECT 1 FROM invoice_categories
            WHERE TRIM(product_name) = ?
              AND TRIM(category) = ?
              AND TRIM(subcategory) = ?
            LIMIT 1
            """,
            (product.name, product.category, product.subcategory),
        ).fetchone()
        if used is None:
            self.conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        else:
            self.conn.execute(
                "UPDATE products SET is_active = 0 WHERE id = ?",
                (product_id,),
            )
        self.conn.commit()

    def _category_id(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM product_categories WHERE name = ?",
            (name,),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO product_categories (name) VALUES (?)",
            (name,),
        )
        return int(cur.lastrowid)

    def _subcategory_id(self, category_id: int, name: str) -> int:
        row = self.conn.execute(
            """
            SELECT id FROM product_subcategories
            WHERE category_id = ? AND name = ?
            """,
            (category_id, name),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cur = self.conn.execute(
            """
            INSERT INTO product_subcategories (category_id, name) VALUES (?, ?)
            """,
            (category_id, name),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _payment_from_row(row: sqlite3.Row) -> SupermarketPayment:
        return SupermarketPayment(
            id=int(row["id"]),
            invoice_id=int(row["invoice_id"]),
            payment_date=date.fromisoformat(row["payment_date"]),
            actual_received_amount=float(row["actual_received_amount"]),
            retro_percentage=float(row["retro_percentage"]),
            delivery_charge_percentage=float(row["delivery_charge_percentage"]),
            tax_percentage=float(row["tax_percentage"]),
            retro_cut_off_date=_str_to_date(row["retro_cut_off_date"]),
        )
