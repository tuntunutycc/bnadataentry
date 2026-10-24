"""Run the Streamlit screens so each page function executes against a temp database."""

from __future__ import annotations

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

os.environ["BNA_PRODUCT_USER"] = "admin"
os.environ["BNA_PRODUCT_PASSWORD"] = "screen-secret"


def _open_products(app: AppTest) -> AppTest:
    next(item for item in app.button if item.label == "Products").click().run()
    username = next(item for item in app.text_input if item.label == "Username")
    password = next(item for item in app.text_input if item.label == "Password")
    username.set_value("admin")
    password.set_value("screen-secret").run()
    next(item for item in app.button if item.label == "Sign in").click().run()
    return app


def reconciliation_screen(db_path: str) -> None:
    from bnadataentry.db import init_db
    from bnadataentry.recon_ui import render_reconciliation

    render_reconciliation(init_db(db_path))


def legacy_screens(db_path: str) -> None:
    from datetime import date

    import app
    from bnadataentry.clients import get_client
    from bnadataentry.db import init_db
    from bnadataentry.models import Invoice, InvoiceSummary, LineItem
    from bnadataentry.repository import InvoiceRepository

    repo = InvoiceRepository(init_db(db_path))
    saved = repo.create_invoice(
        Invoice(
            client_id="capital",
            invoice_no="SCR-1",
            invoice_date=date(2026, 3, 2),
            due_date=date(2026, 4, 1),
            branch="CREL",
            lines=[
                LineItem(
                    product_description="Sardine",
                    pack_qty=2,
                    list_price=10,
                    line_total=20,
                    grand_total=20,
                )
            ],
            summary=InvoiceSummary(
                branch_amounts={"CREL": 20},
                balance_to_receive=19.4,
                retro_amount=-0.6,
            ),
        )
    )
    app.render_browse(repo, "capital", 2026, 2026, None, branch="CREL")
    app.render_summary(repo, "one_stop_mart", 2025, 2026, None)
    app.render_payments(repo, "capital", 2026, 2026, 3, branch="CREL")
    app.render_import_export(repo, "capital", 2026, 2026, None)
    app.render_edit(
        repo,
        get_client("makro"),
        branch="Sat San",
        year_from=2026,
        year_to=2026,
        month=3,
    )
    assert saved.id is not None
    app._fmt_money(189685.44)
    app._render_money_table([("Expected", 189685.44)])
    invoice_date, po_date, due = app._default_dates(saved)
    assert invoice_date == date(2026, 3, 2)
    assert po_date == date(2026, 3, 2)
    assert due == date(2026, 4, 1)
    message = app._friendly_invoice_conflict(
        invoice_no="SCR-1",
        branch="CREL",
        invoice_date=None,
        existing_id=saved.id,
    )
    assert "SCR-1" in message


def test_reconciliation_screen_saves_and_deletes(tmp_path: Path) -> None:
    db_path = tmp_path / "ui.db"
    app = AppTest.from_function(
        reconciliation_screen, kwargs={"db_path": str(db_path)}
    )
    app.run()
    assert not app.exception
    app.radio[0].set_value("Makro").run()
    app.radio[0].set_value("One Stop Mart").run()
    app.radio[0].set_value("Capital").run()
    assert not app.exception

    _open_products(app)
    labels = {item.label: item for item in app.text_input}
    labels["Category"].set_value("Snacks")
    labels["Sub-category"].set_value("Chips")
    labels["Product name"].set_value("Potato chips")
    pieces = next(item for item in app.number_input if item.label == "Pieces per pack")
    price = next(item for item in app.number_input if item.label == "Price per piece")
    pieces.set_value(12)
    price.set_value(10).run()
    next(item for item in app.button if item.label == "Save product").click().run()
    next(item for item in app.button if item.label == "Back to invoices").click().run()
    number = next(item for item in app.text_input if item.label == "Invoice number")
    quantity = next(item for item in app.number_input if item.label == "Quantity")
    number.set_value("UI-1")
    quantity.set_value(2)
    days = next(item for item in app.number_input if item.label == "Credit days")
    days.set_value(7).run()
    next(item for item in app.button if item.label == "Save invoice").click().run()
    assert not app.exception
    next(item for item in app.button if item.label == "Edit").click().run()
    next(item for item in app.button if item.label == "Cancel").click().run()
    next(item for item in app.button if item.label == "Delete").click().run()
    next(item for item in app.button if item.label == "Keep this invoice").click().run()
    assert not app.exception

    received = next(
        item for item in app.number_input if item.label == "Actual received amount"
    )
    received.set_value(100).run()
    next(item for item in app.button if item.label == "Save payment").click().run()
    assert not app.exception

    next(
        item for item in app.button if item.label == "Clear data"
    ).click().run()
    next(item for item in app.button if item.label == "Cancel").click().run()
    assert not app.exception
    next(
        item for item in app.button if item.label == "Clear data"
    ).click().run()
    next(item for item in app.button if item.label == "Yes, clear data").click().run()
    assert not app.exception
    assert any("No saved invoices" in str(item.value) for item in app.info)


def test_capital_product_screen(tmp_path: Path) -> None:
    db_path = tmp_path / "city.db"
    app = AppTest.from_function(
        reconciliation_screen, kwargs={"db_path": str(db_path)}
    )
    app.run()
    assert not app.exception
    next(item for item in app.button if item.label == "Products").click().run()
    username = next(item for item in app.text_input if item.label == "Username")
    password = next(item for item in app.text_input if item.label == "Password")
    username.set_value("admin")
    password.set_value("wrong").run()
    next(item for item in app.button if item.label == "Sign in").click().run()
    assert any("not correct" in str(item.value) for item in app.error)
    assert not any(item.label == "Save product" for item in app.button)
    _open_products(app)
    labels = {item.label: item for item in app.text_input}
    labels["Category"].set_value("Snacks")
    labels["Sub-category"].set_value("Chips")
    labels["Product name"].set_value("Potato chips")
    pieces = next(item for item in app.number_input if item.label == "Pieces per pack")
    price = next(item for item in app.number_input if item.label == "Price per piece")
    pieces.set_value(12)
    price.set_value(10).run()
    next(item for item in app.button if item.label == "Save product").click().run()
    assert not app.exception
    assert any("Potato chips" in str(item.value) for item in app.markdown)
    assert any(item.label == "Delete" for item in app.button)
    next(item for item in app.button if item.label == "Back to invoices").click().run()
    next(item for item in app.button if item.label == "Add line").click().run()
    next(item for item in app.button if item.label == "Remove").click().run()
    next(item for item in app.button if item.label == "Products").click().run()
    next(item for item in app.button if item.label == "Delete").click().run()
    assert not app.exception


def running_app(db_path: str) -> None:
    from pathlib import Path

    import app

    app.DEFAULT_DB_PATH = Path(db_path)
    app._ensure_schema.clear()
    app.main()


def jump_to_edit() -> None:
    import streamlit as st

    import app

    if st.session_state.get("edit_jump"):
        return
    st.session_state.edit_jump = True
    app._goto_edit_invoice(1)


def test_legacy_screens_render(tmp_path: Path) -> None:
    app = AppTest.from_function(
        legacy_screens, kwargs={"db_path": str(tmp_path / "legacy.db")}
    )
    app.run(timeout=20)
    assert not app.exception

    entry = AppTest.from_function(
        running_app, kwargs={"db_path": str(tmp_path / "entry.db")}
    )
    entry.run()
    assert not entry.exception

    jump = AppTest.from_function(jump_to_edit)
    jump.run()
    assert not jump.exception
