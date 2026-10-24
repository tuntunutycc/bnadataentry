"""Repository CRUD tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bnadataentry.db import init_db
from bnadataentry.models import Invoice, InvoiceSummary, LineItem
from bnadataentry.repository import InvoiceRepository


@pytest.fixture
def repo(tmp_path: Path) -> InvoiceRepository:
    conn = init_db(tmp_path / "repo.db")
    return InvoiceRepository(conn)


def _sample_invoice(**kwargs) -> Invoice:
    data = {
        "client_id": "capital",
        "invoice_no": "1028",
        "invoice_date": date(2026, 7, 21),
        "po_date": date(2026, 7, 21),
        "due_date": date(2026, 8, 21),
        "branch": "CREL",
        "product_label": "YMK Sardines",
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


def test_create_invoice_with_lines(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_sample_invoice())
    assert created.id is not None
    loaded = repo.get_invoice(created.id)
    assert loaded is not None
    assert loaded.invoice_no == "1028"
    assert len(loaded.lines) == 1
    assert loaded.lines[0].line_total == pytest.approx(104000.0)
    assert loaded.summary.retro_amount == pytest.approx(-3120.0)


def test_update_summary_override(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_sample_invoice())
    updated = repo.update_summary_overrides(
        created.id,  # type: ignore[arg-type]
        retro_pct_override=5.0,
        remark="manual",
    )
    assert updated.summary.retro_pct_override == 5.0
    assert updated.summary.remark == "manual"
    assert updated.summary.retro_amount == pytest.approx(-5200.0)


def test_filter_by_month(repo: InvoiceRepository) -> None:
    repo.create_invoice(_sample_invoice())
    repo.create_invoice(
        _sample_invoice(
            invoice_no="2001",
            invoice_date=date(2026, 8, 1),
            lines=[LineItem(product_description="X", pack_qty=1, list_price=10)],
        )
    )
    july = repo.list_invoices(client_id="capital", year=2026, month=7)
    assert len(july) == 1
    assert july[0].invoice_no == "1028"


def test_filter_by_year_range(repo: InvoiceRepository) -> None:
    repo.create_invoice(
        _sample_invoice(invoice_no="0121", invoice_date=date(2025, 12, 15))
    )
    repo.create_invoice(
        _sample_invoice(invoice_no="1028", invoice_date=date(2026, 7, 21))
    )
    repo.create_invoice(
        _sample_invoice(invoice_no="9999", invoice_date=date(2024, 1, 1))
    )
    spanned = repo.list_invoices(
        client_id="capital", year_from=2025, year_to=2026
    )
    nos = {inv.invoice_no for inv in spanned}
    assert nos == {"0121", "1028"}
    single = repo.list_invoices(
        client_id="capital", year_from=2026, year_to=2026
    )
    assert len(single) == 1
    assert single[0].invoice_no == "1028"


def test_filter_by_branch(repo: InvoiceRepository) -> None:
    repo.create_invoice(_sample_invoice(branch="CREL", invoice_no="1028"))
    repo.create_invoice(
        _sample_invoice(
            branch="G&G",
            invoice_no="1029",
            invoice_date=date(2026, 7, 21),
            lines=[LineItem(product_description="Yobick", pack_qty=10, list_price=392)],
        )
    )
    repo.create_invoice(
        _sample_invoice(
            branch="Hyper",
            invoice_no="1159",
            invoice_date=date(2026, 8, 14),
            lines=[LineItem(product_description="Konjac", pack_qty=50, list_price=1008)],
        )
    )

    all_capital = repo.list_invoices(client_id="capital", year=2026)
    assert len(all_capital) == 3

    crel = repo.list_invoices(client_id="capital", branch="CREL")
    assert len(crel) == 1
    assert crel[0].invoice_no == "1028"
    assert crel[0].branch == "CREL"

    gng = repo.list_invoices(client_id="capital", branch="G&G", year=2026, month=7)
    assert len(gng) == 1
    assert gng[0].invoice_no == "1029"


def test_cascade_delete(repo: InvoiceRepository) -> None:
    created = repo.create_invoice(_sample_invoice())
    assert created.id is not None
    repo.delete_invoice(created.id)
    assert repo.get_invoice(created.id) is None
    assert repo.count_line_items() == 0
    row = repo.conn.execute(
        "SELECT COUNT(*) AS n FROM invoice_summary"
    ).fetchone()
    assert row["n"] == 0


def test_upsert_is_idempotent(repo: InvoiceRepository) -> None:
    repo.upsert_invoice(_sample_invoice())
    repo.upsert_invoice(
        _sample_invoice(
            lines=[
                LineItem(
                    product_description="Updated",
                    pack_qty=100,
                    list_price=520,
                )
            ]
        )
    )
    assert repo.count_invoices("capital") == 1
    inv = repo.find_by_key("capital", "1028", date(2026, 7, 21), branch="CREL")
    assert inv is not None
    assert inv.lines[0].product_description == "Updated"
    assert inv.lines[0].line_total == pytest.approx(52000.0)
