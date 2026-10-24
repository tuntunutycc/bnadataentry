"""Payment ledger and AR snapshot tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bnadataentry.db import init_db
from bnadataentry.models import Invoice, InvoiceSummary, LineItem, Payment
from bnadataentry.payments import (
    STATUS_OVERPAID,
    STATUS_PAID,
    STATUS_PARTIAL,
    STATUS_UNPAID,
    ar_snapshot,
    payment_status,
)
from bnadataentry.repository import InvoiceRepository


@pytest.fixture
def repo(tmp_path: Path) -> InvoiceRepository:
    conn = init_db(tmp_path / "payments.db")
    return InvoiceRepository(conn)


def _invoice(**kwargs) -> Invoice:
    data = {
        "client_id": "capital",
        "invoice_no": "1028",
        "invoice_date": date(2026, 7, 21),
        "due_date": date(2026, 8, 21),
        "branch": "CREL",
        "lines": [
            LineItem(
                product_description="Yomae Krua Sardine",
                pack_qty=200,
                list_price=520,
            )
        ],
        "summary": InvoiceSummary(),
    }
    data.update(kwargs)
    return Invoice(**data)


def test_payment_status_math() -> None:
    assert payment_status(1000, 0) == STATUS_UNPAID
    assert payment_status(1000, 400) == STATUS_PARTIAL
    assert payment_status(1000, 1000) == STATUS_PAID
    assert payment_status(1000, 1200) == STATUS_OVERPAID


def test_ar_snapshot_overdue() -> None:
    inv = _invoice(due_date=date(2020, 1, 1))
    inv.summary.balance_to_receive = 5000.0
    snap = ar_snapshot(
        inv, amount_paid=0.0, today=date(2026, 9, 25)
    )
    assert snap["status"] == STATUS_UNPAID
    assert snap["is_overdue"] is True
    assert snap["outstanding"] == pytest.approx(5000.0)

    snap_paid = ar_snapshot(
        inv, amount_paid=5000.0, today=date(2026, 9, 25)
    )
    assert snap_paid["status"] == STATUS_PAID
    assert snap_paid["is_overdue"] is False


def test_capital_amount_due_subtracts_dc_at_payment() -> None:
    """Capital: Amount Due = invoice balance − delivery charge (payment stage)."""
    inv = _invoice()
    inv.summary.balance_to_receive = 100880.0
    inv.summary.delivery_charge = 0.0
    snap0 = ar_snapshot(inv, amount_paid=0.0)
    assert snap0["dc_at_payment"] is True
    assert snap0["invoice_balance"] == pytest.approx(100880.0)
    assert snap0["amount_due"] == pytest.approx(100880.0)

    inv.summary.delivery_charge = 3843.05
    snap = ar_snapshot(inv, amount_paid=0.0)
    assert snap["delivery_charge"] == pytest.approx(3843.05)
    assert snap["amount_due"] == pytest.approx(97036.95)
    assert snap["outstanding"] == pytest.approx(97036.95)


def test_create_list_sum_payments(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_invoice())
    assert created.id is not None
    due = float(created.summary.balance_to_receive or 0)

    p1 = repo.create_payment(
        Payment(
            invoice_id=created.id,
            payment_date=date(2026, 8, 1),
            amount=due / 2,
            method="bank",
            reference="TRX-1",
        )
    )
    assert p1.id is not None
    assert len(repo.list_payments(created.id)) == 1

    paid = repo.sum_paid_by_invoice_ids([created.id])
    assert paid[created.id] == pytest.approx(due / 2)

    snap = ar_snapshot(created, amount_paid=paid[created.id])
    assert snap["status"] == STATUS_PARTIAL

    repo.create_payment(
        Payment(
            invoice_id=created.id,
            payment_date=date(2026, 8, 15),
            amount=due / 2,
            method="cash",
        )
    )
    paid2 = repo.sum_paid_by_invoice_ids([created.id])[created.id]
    assert ar_snapshot(created, amount_paid=paid2)["status"] == STATUS_PAID


def test_update_and_delete_payment(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_invoice())
    assert created.id is not None
    payment = repo.create_payment(
        Payment(
            invoice_id=created.id,
            payment_date=date(2026, 8, 1),
            amount=1000.0,
            method="cheque",
            reference="CHQ-9",
        )
    )
    assert payment.id is not None
    payment.amount = 1500.0
    payment.method = "transfer"
    updated = repo.update_payment(payment)
    assert updated.amount == pytest.approx(1500.0)
    assert updated.method == "transfer"

    repo.delete_payment(payment.id)
    assert repo.list_payments(created.id) == []
    assert repo.sum_paid_by_invoice_ids([created.id]) == {}


def test_payments_cascade_delete_with_invoice(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_invoice())
    assert created.id is not None
    repo.create_payment(
        Payment(
            invoice_id=created.id,
            payment_date=date(2026, 9, 1),
            amount=100.0,
            method="cash",
        )
    )
    assert len(repo.list_payments(created.id)) == 1
    repo.delete_invoice(created.id)
    assert repo.list_payments(created.id) == []


def test_reject_non_positive_payment(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_invoice())
    assert created.id is not None
    with pytest.raises(ValueError, match="greater than zero"):
        repo.create_payment(
            Payment(
                invoice_id=created.id,
                payment_date=date(2026, 9, 1),
                amount=0,
                method="cash",
            )
        )


def test_list_payments_for_invoices(repo: InvoiceRepository) -> None:
    a = repo.create_invoice(_invoice(invoice_no="A1"))
    b = repo.create_invoice(
        _invoice(
            invoice_no="B1",
            invoice_date=date(2026, 7, 22),
            due_date=date(2026, 9, 30),
        )
    )
    assert a.id and b.id
    repo.create_payment(
        Payment(
            invoice_id=a.id,
            payment_date=date(2026, 9, 1),
            amount=10,
            method="cash",
        )
    )
    repo.create_payment(
        Payment(
            invoice_id=b.id,
            payment_date=date(2026, 9, 2),
            amount=20,
            method="bank",
        )
    )
    listed = repo.list_payments_for_invoices([a.id, b.id])
    assert len(listed) == 2
    assert {p.invoice_id for p in listed} == {a.id, b.id}
