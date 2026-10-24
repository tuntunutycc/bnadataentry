"""Accounts-receivable helpers: paid / outstanding / status from payments."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from bnadataentry.calculations import dc_applies_at_payment
from bnadataentry.models import Invoice, Payment

PAYMENT_METHODS = ("cash", "bank", "cheque", "transfer")

STATUS_UNPAID = "unpaid"
STATUS_PARTIAL = "partial"
STATUS_PAID = "paid"
STATUS_OVERPAID = "overpaid"

STATUS_LABELS = {
    STATUS_UNPAID: "Unpaid",
    STATUS_PARTIAL: "Partial",
    STATUS_PAID: "Paid",
    STATUS_OVERPAID: "Overpaid",
}


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def payment_status(amount_due: float, amount_paid: float) -> str:
    """Derive payment status from due vs paid amounts."""
    due = round(float(amount_due or 0.0), 6)
    paid = round(float(amount_paid or 0.0), 6)
    if paid <= 0:
        return STATUS_UNPAID
    if paid < due - 1e-9:
        return STATUS_PARTIAL
    if paid > due + 1e-9:
        return STATUS_OVERPAID
    return STATUS_PAID


def ar_snapshot(
    invoice: Invoice,
    *,
    amount_paid: float | None = None,
    payments: list[Payment] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Compute AR fields for an invoice.

    Pass either ``amount_paid`` or ``payments`` (summed). Does not mutate
    ``balance_to_receive``.

    For Capital, ``balance_to_receive`` is the invoice balance after retro
    (before DC); Amount Due = invoice balance − delivery_charge.
    """
    invoice_balance = round(float(invoice.summary.balance_to_receive or 0.0), 2)
    delivery_charge = abs(float(invoice.summary.delivery_charge or 0.0))
    if dc_applies_at_payment(invoice.client_id):
        amount_due = round(invoice_balance - delivery_charge, 2)
    else:
        # Non-Capital: DC (if any) is already inside balance_to_receive.
        amount_due = invoice_balance
        delivery_charge = 0.0
    if amount_paid is None:
        amount_paid = round(
            sum(float(p.amount or 0.0) for p in (payments or [])), 2
        )
    else:
        amount_paid = round(float(amount_paid or 0.0), 2)

    outstanding = round(amount_due - amount_paid, 2)
    status = payment_status(amount_due, amount_paid)
    as_of = today or utc_today()
    is_overdue = bool(
        outstanding > 1e-9
        and invoice.due_date is not None
        and invoice.due_date < as_of
    )
    return {
        "invoice_balance": invoice_balance,
        "delivery_charge": delivery_charge,
        "amount_due": amount_due,
        "amount_paid": amount_paid,
        "outstanding": outstanding,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "is_overdue": is_overdue,
        "dc_at_payment": dc_applies_at_payment(invoice.client_id),
    }
