"""Streamlit screens for supermarket invoice reconciliation."""

from __future__ import annotations

import hmac
import os
import sqlite3
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from bnadataentry.expected_payment import (
    CAPITAL,
    MAKRO,
    ONE_STOP_MART,
    SUPERMARKETS,
    PaymentTerms,
    expected_payment,
    money,
)
from bnadataentry.payments import utc_today
from bnadataentry.recon_export import export_filename, reconciliation_workbook_bytes
from bnadataentry.reconciliation import (
    UNIT_PIECE,
    UNITS,
    InvoiceCategory,
    Product,
    ReconciliationRepository,
    SupermarketInvoice,
    SupermarketPayment,
    capital_delivery_rate,
    discount_note,
    exempt_amount,
    shops_for,
)


def render_reconciliation(conn: sqlite3.Connection) -> None:
    repo = ReconciliationRepository(conn)
    supermarket, shop = _shop_menu()
    _render_clear_button(repo)
    if st.session_state.get("show_products"):
        _render_products(repo)
        return
    st.subheader(f"{supermarket} · {shop}")
    st.caption(
        "This page is only this shop. Enter an invoice, then record the payment."
    )
    _render_saved(repo, supermarket, shop)
    names = [
        "New invoice",
        "Record payment",
        "Reconciliation report",
        "Category summary",
    ]
    tabs = st.tabs(names)
    with tabs[0]:
        _render_invoice_form(repo, supermarket, shop)
    with tabs[1]:
        _render_payment_form(repo, supermarket, shop)
    with tabs[2]:
        _render_report(repo, supermarket, shop)
    with tabs[3]:
        _render_category_summary(repo, supermarket, shop)


def _shop_menu() -> tuple[str, str]:
    """Sidebar: supermarket, then one submenu item per shop."""
    with st.sidebar:
        st.header("Supermarket")
        supermarket = st.radio(
            "Supermarket",
            SUPERMARKETS,
            key="menu_supermarket",
            label_visibility="collapsed",
            on_change=_leave_products,
        )
        shops = shops_for(supermarket)
        st.header("Shop")
        shop = st.radio(
            "Shop",
            shops,
            key=f"menu_shop_{supermarket}",
            label_visibility="collapsed",
            on_change=_leave_products,
        )
        st.divider()
        st.caption("Shared by every supermarket")
        if st.button("Products", key="open_products"):
            st.session_state["show_products"] = True
            st.rerun()
    return supermarket, shop


def _leave_products() -> None:
    st.session_state["show_products"] = False


def _render_clear_button(repo: ReconciliationRepository) -> None:
    """Sidebar control that wipes entered data for a fresh test."""
    with st.sidebar:
        st.divider()
        if st.button("Clear data", key="clear_test_data"):
            st.session_state["confirm_clear_test_data"] = True
        if st.session_state.get("confirm_clear_test_data"):
            st.warning(
                "This deletes every invoice, payment, and product."
            )
            if st.button("Yes, clear data", key="confirm_clear_yes", type="primary"):
                invoices, payments, products = repo.clear_all_data()
                st.session_state["confirm_clear_test_data"] = False
                st.success(
                    f"Cleared {invoices} invoices, {payments} payments, "
                    f"and {products} products."
                )
                st.rerun()
            if st.button("Cancel", key="confirm_clear_no"):
                st.session_state["confirm_clear_test_data"] = False
                st.rerun()


def _render_saved(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    """Show this shop's invoices. Choose one, then edit it."""
    rows = repo.report_rows(supermarket, shop)
    if not rows:
        st.info(f"No saved invoices for {shop} yet.")
        return
    _render_edit_controls(repo, rows)
    st.markdown(f"**Saved invoices and payments for {shop}**")
    _show_report_table(rows, supermarket)


def _render_edit_controls(
    repo: ReconciliationRepository,
    rows: list[dict[str, object]],
) -> None:
    """Choose one invoice from this shop, then open its edit form."""
    if not rows:
        return
    labels: dict[str, int] = {}
    for row in rows:
        amount = float(row["Total Invoice Amount"])  # type: ignore[arg-type]
        label = (
            f"{row['Invoice No.']} · {row['Supermarket']} · {row['Shop']} · "
            f"{amount:,.2f}"
        )
        labels[label] = int(row["id"])  # type: ignore[arg-type]
    choice = st.selectbox("Choose an invoice", list(labels), key="edit_invoice_choice")
    chosen_id = labels[choice]
    edit_col, delete_col = st.columns(2)
    if edit_col.button("Edit", key="edit_chosen_invoice"):
        st.session_state["editing_invoice_id"] = chosen_id
        st.session_state["deleting_invoice_id"] = None
    if delete_col.button("Delete", key="delete_chosen_invoice"):
        st.session_state["deleting_invoice_id"] = chosen_id
        st.session_state["editing_invoice_id"] = None
    if st.session_state.get("deleting_invoice_id") == chosen_id:
        st.warning(
            "Delete this invoice? Its payments and category lines are removed. "
            "Other invoices stay."
        )
        yes_col, no_col = st.columns(2)
        if yes_col.button("Yes, delete this invoice", key="confirm_delete_invoice"):
            repo.delete_invoice(chosen_id)
            st.session_state["deleting_invoice_id"] = None
            st.session_state["editing_invoice_id"] = None
            st.rerun()
        if no_col.button("Keep this invoice", key="cancel_delete_invoice"):
            st.session_state["deleting_invoice_id"] = None
            st.rerun()
        return
    if st.session_state.get("editing_invoice_id") != chosen_id:
        return
    invoice = repo.get_invoice(chosen_id)
    if invoice is None:
        return
    _render_edit_form(repo, invoice)


def _render_edit_form(
    repo: ReconciliationRepository, invoice: SupermarketInvoice
) -> None:
    assert invoice.id is not None
    st.markdown(f"**Edit invoice {invoice.invoice_number}**")
    tax_treatment = invoice.tax_treatment
    tax_percentage = invoice.tax_percentage
    retro_percentage = invoice.retro_percentage
    if invoice.supermarket_name == MAKRO:
        tax_percentage, retro_percentage = _makro_rate_inputs(
            f"edit_{invoice.id}",
            tax_pct=invoice.tax_percentage * 100,
            retro_pct=invoice.retro_percentage * 100,
        )
    prefix = f"inv_edit_{invoice.id}"
    invoice_number = st.text_input(
        "Invoice number",
        value=invoice.invoice_number,
        key=f"{prefix}_number",
    )
    invoice_date, due_date, credit_days = _schedule_inputs(
        prefix,
        invoice.invoice_date,
        invoice.due_date,
        invoice.credit_days,
    )
    total, categories, discount, remark = _invoice_lines(
        repo,
        invoice.supermarket_name,
        prefix,
        existing=invoice.categories,
        total=float(invoice.total_invoice_amount),
        discount=float(invoice.discount_percentage),
        remark=invoice.remark,
    )
    save_col, cancel_col = st.columns(2)
    submitted = save_col.button("Save changes", key=f"{prefix}_save")
    cancelled = cancel_col.button("Cancel", key=f"{prefix}_cancel")
    if cancelled:
        _clear_prefix(prefix)
        st.session_state["editing_invoice_id"] = None
        st.rerun()
    if not submitted:
        return
    try:
        repo.update_invoice(
            SupermarketInvoice(
                id=invoice.id,
                invoice_number=invoice_number,
                supermarket_name=invoice.supermarket_name,
                shop=invoice.shop,
                invoice_date=invoice_date,
                due_date=due_date,
                total_invoice_amount=total,
                tax_treatment=tax_treatment,
                tax_percentage=tax_percentage,
                retro_percentage=retro_percentage,
                discount_percentage=discount,
                remark=remark,
                credit_days=credit_days,
                categories=categories,
            )
        )
    except ValueError as exc:
        st.error(str(exc))
    else:
        _clear_prefix(prefix)
        st.session_state["editing_invoice_id"] = None
        st.success(f"Updated invoice {invoice_number.strip()}.")
        st.rerun()


def _render_invoice_form(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    today = utc_today()
    st.caption(f"New invoice for {supermarket} · {shop}")
    if supermarket == CAPITAL:
        st.caption("Capital item prices already include 5% tax.")
    tax_treatment = ""
    tax_percentage = 0.0
    retro_percentage = 0.0
    if supermarket == MAKRO:
        tax_percentage, retro_percentage = _makro_rate_inputs(
            "new",
            tax_pct=5.0,
            retro_pct=5.0,
        )
    prefix = f"inv_new_{supermarket}_{shop}"
    invoice_number = st.text_input("Invoice number", key=f"{prefix}_number")
    invoice_date, due_date, credit_days = _schedule_inputs(
        prefix, today, today + timedelta(days=30), 30
    )
    total, categories, discount, remark = _invoice_lines(
        repo, supermarket, prefix, existing=[], total=0.0, discount=0.0, remark=""
    )
    submitted = st.button("Save invoice", type="primary", key=f"{prefix}_save")
    if submitted:
        try:
            repo.create_invoice(
                SupermarketInvoice(
                    invoice_number=invoice_number,
                    supermarket_name=supermarket,
                    shop=shop,
                    invoice_date=invoice_date,
                    due_date=due_date,
                    total_invoice_amount=total,
                    tax_treatment=tax_treatment,
                    tax_percentage=tax_percentage,
                    retro_percentage=retro_percentage,
                    discount_percentage=discount,
                    remark=remark,
                    credit_days=credit_days,
                    categories=categories,
                )
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            _clear_prefix(prefix)
            st.success(f"Saved invoice {invoice_number.strip()}. It is in the table above.")
            st.rerun()


def _render_payment_form(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    invoices = repo.list_invoices(supermarket, shop)
    if not invoices:
        st.info(f"Save an invoice for {shop} first, then record the payment.")
        return

    labels = {
        f"{inv.invoice_number} · {inv.supermarket_name} · {inv.shop} · {inv.total_invoice_amount:,.2f}": inv
        for inv in invoices
        if inv.id is not None
    }
    choice = st.selectbox(
        "Invoice",
        list(labels.keys()),
        key=f"recon_payment_invoice_{supermarket}_{shop}",
    )
    invoice = labels[choice]
    assert invoice.id is not None

    capital_retro_pct = 3.0
    if invoice.supermarket_name == CAPITAL:
        recorded = repo.list_payments(invoice.id)
        if recorded:
            latest = max(recorded, key=lambda p: (p.payment_date, p.id or 0))
            capital_retro_pct = latest.retro_percentage * 100
    retro_rate, delivery_rate, tax_rate, cut_off = _rate_inputs(
        invoice, capital_retro_pct=capital_retro_pct
    )
    terms = PaymentTerms(
        total_invoice_amount=invoice.total_invoice_amount,
        is_tax_included=invoice.is_tax_included,
        tax_treatment=invoice.tax_treatment,
        tax_percentage=tax_rate,
        retro_percentage=retro_rate,
        delivery_charge_percentage=delivery_rate,
        discount_percentage=invoice.discount_percentage,
        exempt_amount=exempt_amount(invoice),
    )
    preview = expected_payment(invoice.supermarket_name, terms)
    st.markdown("**Expected payment**")
    st.dataframe(
        pd.DataFrame(
            [{"Step": label, "Amount": value} for label, value in preview.steps]
        ),
        use_container_width=True,
        hide_index=True,
    )

    actual = st.number_input(
        "Actual received amount",
        min_value=0.0,
        step=100.0,
        format="%.2f",
        key="recon_actual_received",
    )
    paid_on = st.date_input("Payment date", value=utc_today(), key="recon_paid_on")
    variance = money(actual - preview.expected_final_payment)
    st.metric("Variance (received − expected)", f"{variance:,.2f}")

    if st.button("Save payment", type="primary"):
        try:
            repo.create_payment(
                SupermarketPayment(
                    invoice_id=invoice.id,
                    payment_date=paid_on,
                    actual_received_amount=actual,
                    retro_percentage=retro_rate,
                    delivery_charge_percentage=delivery_rate,
                    tax_percentage=tax_rate,
                    retro_cut_off_date=cut_off,
                )
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"Saved payment of {actual:,.2f} against {invoice.invoice_number}."
            )
            st.rerun()

    existing = repo.list_payments(invoice.id)
    if existing:
        st.markdown("**Payments already recorded**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Date": p.payment_date.isoformat(),
                        "Received": p.actual_received_amount,
                        **(
                            {"Tax %": p.tax_percentage * 100}
                            if supermarket == MAKRO
                            else {}
                        ),
                        "Retro %": p.retro_percentage * 100,
                        "Delivery %": p.delivery_charge_percentage * 100,
                        "Retro cut-off": (
                            p.retro_cut_off_date.isoformat()
                            if p.retro_cut_off_date
                            else ""
                        ),
                    }
                    for p in existing
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def _clear_prefix(prefix: str) -> None:
    for key in list(st.session_state):
        if str(key).startswith(prefix):
            del st.session_state[key]


def _schedule_inputs(
    prefix: str, invoice_date: date, due: date, credit_days: int
) -> tuple[date, date, int]:
    """Credit days set the due date. The due date can still be changed by hand."""
    date_key = f"{prefix}_invoice_date"
    due_key = f"{prefix}_due_date"
    days_key = f"{prefix}_credit_days"
    if date_key not in st.session_state:
        st.session_state[date_key] = invoice_date
        st.session_state[due_key] = due
        st.session_state[days_key] = int(credit_days)

    def _sync() -> None:
        current = st.session_state[date_key]
        days = int(st.session_state[days_key])
        st.session_state[due_key] = current + timedelta(days=max(days, 0))

    c1, c2, c3 = st.columns(3)
    chosen_date = c1.date_input("Invoice date", key=date_key, on_change=_sync)
    days = c2.number_input(
        "Credit days",
        min_value=0,
        step=1,
        key=days_key,
        on_change=_sync,
    )
    chosen_due = c3.date_input(
        "Due date",
        key=due_key,
        help="Starts as the invoice date plus the credit days. Change it if needed.",
    )
    return chosen_date, chosen_due, int(days)


def _invoice_lines(
    repo: ReconciliationRepository,
    supermarket: str,
    prefix: str,
    *,
    existing: list[InvoiceCategory],
    total: float,
    discount: float,
    remark: str,
) -> tuple[float, list[InvoiceCategory], float, str]:
    categories = _city_line_editor(repo, prefix, existing, tax_exempt=True)
    line_total = money(sum(line.amount for line in categories))
    st.session_state[f"{prefix}_total"] = line_total
    st.markdown(f"**{supermarket} invoice total** {_fmt_money(line_total)}")
    chosen_discount, chosen_remark = _discount_inputs(
        prefix, line_total, discount, remark
    )
    return line_total, categories, chosen_discount, chosen_remark


def _sync_discount_remark(prefix: str) -> None:
    rate = float(st.session_state.get(f"{prefix}_discount", 0) or 0)
    if rate <= 0:
        return
    current = str(st.session_state.get(f"{prefix}_remark", ""))
    if current.strip() and not current.startswith("Discount "):
        return
    amount = float(st.session_state.get(f"{prefix}_total", 0) or 0)
    st.session_state[f"{prefix}_remark"] = discount_note(amount, rate)


_DISCOUNTS = (0.0, 0.03, 0.04, 0.05)


def _discount_label(rate: float) -> str:
    if rate <= 0:
        return "None"
    return f"{rate * 100:.0f}%"


def _discount_inputs(
    prefix: str, total: float, saved_rate: float, saved_remark: str
) -> tuple[float, str]:
    rate_key = f"{prefix}_discount"
    remark_key = f"{prefix}_remark"
    if rate_key not in st.session_state:
        st.session_state[rate_key] = float(saved_rate)
        st.session_state[remark_key] = saved_remark
    choice = st.selectbox(
        "Discount",
        _DISCOUNTS,
        format_func=_discount_label,
        key=rate_key,
        on_change=_sync_discount_remark,
        args=(prefix,),
    )
    if float(choice) > 0 and not str(st.session_state.get(remark_key, "")).strip():
        st.session_state[remark_key] = discount_note(total, float(choice))
    remark = st.text_input(
        "Remark",
        key=remark_key,
        help="Filled with the discount amount. You can change the wording.",
    )
    return float(choice), str(remark)


def _city_line_editor(
    repo: ReconciliationRepository,
    prefix: str,
    existing: list[InvoiceCategory],
    *,
    tax_exempt: bool = False,
) -> list[InvoiceCategory]:
    """Cascading product lines. Tab order is category, sub-category, product, quantity, unit."""
    active = repo.list_products()
    if not active and not any(str(line.product_name).strip() for line in existing):
        st.info("Add products from Products in the left pane before entering an invoice.")
        return []
    ready_key = f"{prefix}_lines_ready"
    count_key = f"{prefix}_count"
    if ready_key not in st.session_state:
        st.session_state[ready_key] = True
        st.session_state[count_key] = max(len(existing), 1)
        for index, line in enumerate(existing):
            st.session_state[f"{prefix}_cat_{index}"] = line.category
            st.session_state[f"{prefix}_sub_{index}"] = line.subcategory
            st.session_state[f"{prefix}_prod_{index}"] = line.product_name
            st.session_state[f"{prefix}_qty_{index}"] = float(
                line.entered_quantity or line.quantity
            )
            st.session_state[f"{prefix}_unit_{index}"] = line.unit or UNIT_PIECE
            if tax_exempt:
                st.session_state[f"{prefix}_exempt_{index}"] = bool(line.tax_exempt)
    count = int(st.session_state[count_key])
    lines: list[InvoiceCategory] = []
    for index in range(count):
        products = _products_for_line(active, existing, index)
        if not products:
            st.info(
                "Add products from Products in the left pane before entering an invoice."
            )
            continue
        categories = sorted({item.category for item in products})
        st.markdown(f"**Line {index + 1}**")
        category = _choice(
            "Category",
            categories,
            f"{prefix}_cat_{index}",
            existing[index].category if index < len(existing) else categories[0],
        )
        subs = sorted(
            {item.subcategory for item in products if item.category == category}
        )
        subcategory = _choice(
            "Sub-category",
            subs,
            f"{prefix}_sub_{index}",
            existing[index].subcategory if index < len(existing) else subs[0],
        )
        names = sorted(
            item.name
            for item in products
            if item.category == category and item.subcategory == subcategory
        )
        product_name = _choice(
            "Product",
            names,
            f"{prefix}_prod_{index}",
            existing[index].product_name if index < len(existing) else names[0],
        )
        qty_key = f"{prefix}_qty_{index}"
        unit_key = f"{prefix}_unit_{index}"
        if qty_key not in st.session_state:
            st.session_state[qty_key] = 0.0
        if unit_key not in st.session_state:
            st.session_state[unit_key] = UNIT_PIECE
        q1, q2, q3 = st.columns([2, 2, 1])
        quantity = q1.number_input(
            "Quantity", min_value=0.0, step=1.0, key=qty_key
        )
        unit = q2.selectbox("Unit", UNITS, key=unit_key)
        exempt = False
        if tax_exempt:
            exempt = q3.checkbox("Tax exempt", key=f"{prefix}_exempt_{index}")
        if st.button("Remove", key=f"{prefix}_remove_{index}") and count > 1:
            _remove_city_line(prefix, index, count)
            st.rerun()
        match = next(
            item
            for item in products
            if item.category == category
            and item.subcategory == subcategory
            and item.name == product_name
        )
        unit_price = (
            match.price_per_piece
            if unit == UNIT_PIECE
            else match.price_per_piece * match.pieces_per_pack
        )
        pieces = quantity if unit == UNIT_PIECE else quantity * match.pieces_per_pack
        amount = money(quantity * unit_price)
        st.caption(f"{_fmt_qty(pieces)} pieces · {_fmt_money(amount)}")
        if quantity > 0:
            lines.append(
                InvoiceCategory(
                    category=match.category,
                    subcategory=match.subcategory,
                    product_name=match.name,
                    quantity=pieces,
                    amount=amount,
                    unit=str(unit),
                    entered_quantity=float(quantity),
                    pieces_per_pack=match.pieces_per_pack,
                    price_per_piece=match.price_per_piece,
                    base_quantity=pieces,
                    tax_exempt=bool(exempt),
                )
            )
    if st.button("Add line", key=f"{prefix}_add"):
        st.session_state[count_key] = count + 1
        st.rerun()
    return lines


def _products_for_line(
    active: list[Product], existing: list[InvoiceCategory], index: int
) -> list[Product]:
    """Active products, plus this line's saved product when it was hidden later."""
    products = list(active)
    if index >= len(existing):
        return products
    line = existing[index]
    name = str(line.product_name).strip()
    pieces = float(line.pieces_per_pack or 0)
    if not name or pieces <= 0:
        return products
    key = (line.category, line.subcategory, name)
    if any((item.category, item.subcategory, item.name) == key for item in products):
        return products
    products.append(
        Product(
            category=line.category,
            subcategory=line.subcategory,
            name=name,
            pieces_per_pack=pieces,
            price_per_piece=float(line.price_per_piece or 0),
            is_active=False,
        )
    )
    return products


def _choice(label: str, options: list[str], key: str, fallback: str) -> str:
    if key in st.session_state and st.session_state[key] not in options:
        st.session_state[key] = options[0]
    index = options.index(fallback) if fallback in options else 0
    if key not in st.session_state:
        return st.selectbox(label, options, index=index, key=key)
    return st.selectbox(label, options, key=key)


def _remove_city_line(prefix: str, index: int, count: int) -> None:
    fields = ("cat", "sub", "prod", "qty", "unit", "exempt")
    for cursor in range(index, count - 1):
        for field in fields:
            source = f"{prefix}_{field}_{cursor + 1}"
            target = f"{prefix}_{field}_{cursor}"
            if source in st.session_state:
                st.session_state[target] = st.session_state[source]
    for field in fields:
        st.session_state.pop(f"{prefix}_{field}_{count - 1}", None)
    st.session_state[f"{prefix}_count"] = count - 1


def product_admin_credentials() -> tuple[str, str]:
    """Username and password for changing the shared product list."""
    return (
        os.environ.get("BNA_PRODUCT_USER", ""),
        os.environ.get("BNA_PRODUCT_PASSWORD", ""),
    )


def _same_secret(given: str, expected: str) -> bool:
    if not expected or len(given) != len(expected):
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def _render_products(repo: ReconciliationRepository) -> None:
    st.header("Products")
    st.caption(
        "Product list shared by Capital, Makro, and One Stop Mart. "
        "A later price or pack-size change does not rewrite invoices already saved."
    )
    if st.button("Back to invoices", key="close_products"):
        st.session_state["show_products"] = False
        st.rerun()
    signed_in = bool(st.session_state.get("product_admin_ok"))
    if signed_in:
        _render_product_editor(repo)
    else:
        _render_product_login()
    _render_product_list(repo, allow_delete=signed_in)


def _render_product_login() -> None:
    st.caption("Sign in to add or delete products. Invoice entry does not need this.")
    username = st.text_input("Username", key="product_login_user")
    password = st.text_input("Password", type="password", key="product_login_password")
    if not st.button("Sign in", key="product_sign_in"):
        return
    expected_user, expected_password = product_admin_credentials()
    if not expected_user or not expected_password:
        st.error("Product sign-in is not configured on this server.")
        return
    if _same_secret(username.strip(), expected_user) and _same_secret(
        password, expected_password
    ):
        st.session_state["product_admin_ok"] = True
        st.session_state.pop("product_login_password", None)
        st.rerun()
    st.error("Username or password is not correct.")


def _render_product_editor(repo: ReconciliationRepository) -> None:
    if st.button("Sign out", key="product_sign_out"):
        st.session_state["product_admin_ok"] = False
        st.rerun()
    category = st.text_input("Category", key="product_category")
    subcategory = st.text_input("Sub-category", key="product_subcategory")
    name = st.text_input("Product name", key="product_name")
    pieces = st.number_input(
        "Pieces per pack", min_value=0.0, step=1.0, key="product_pieces"
    )
    price = st.number_input(
        "Price per piece",
        min_value=0.0,
        step=1.0,
        format="%.2f",
        key="product_price",
    )
    if st.button("Save product", type="primary"):
        try:
            repo.save_product(
                Product(
                    category=category,
                    subcategory=subcategory,
                    name=name,
                    pieces_per_pack=float(pieces),
                    price_per_piece=float(price),
                )
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            for key in (
                "product_category",
                "product_subcategory",
                "product_name",
                "product_pieces",
                "product_price",
            ):
                st.session_state.pop(key, None)
            st.rerun()


def _render_product_list(
    repo: ReconciliationRepository, *, allow_delete: bool
) -> None:
    products = repo.list_products()
    if not products:
        st.info("No products yet.")
        return
    headers = [
        "Category",
        "Sub-category",
        "Product",
        "Pieces per pack",
        "Price per piece",
    ]
    weights = [2, 2, 2, 1.5, 1.5]
    if allow_delete:
        headers.append("Delete")
        weights.append(1)
    header = st.columns(weights)
    for title, column in zip(headers, header):
        column.markdown(f"**{title}**")
    for product in products:
        row = st.columns(weights)
        values = [
            product.category,
            product.subcategory,
            product.name,
            _fmt_qty(product.pieces_per_pack),
            _fmt_money(product.price_per_piece),
        ]
        for column, value in zip(row, values):
            column.write(value)
        if allow_delete and row[-1].button(
            "Delete", key=f"delete_product_{product.id}"
        ):
            assert product.id is not None
            repo.delete_product(product.id)
            st.rerun()


def _category_editor(
    lines: list[InvoiceCategory], *, key: str, tax_exempt: bool = False
) -> list[InvoiceCategory]:
    """Editable category rows. One invoice can have several categories."""
    st.caption(
        "Add a row for each category on this invoice. "
        "For one category, leave Amount at 0 to use the invoice total. "
        "For several categories, enter each category's amount."
    )
    if tax_exempt:
        st.caption(
            "Tick Tax exempt for goods such as dairy. "
            "Those amounts skip the divide-by-105 delivery step."
        )
    blank: dict[str, object] = {"Category": "", "Quantity": 0.0, "Amount": 0.0}
    if tax_exempt:
        blank["Tax exempt"] = False
    starting = [
        {
            "Category": line.category,
            "Quantity": float(line.quantity),
            "Amount": float(line.amount),
            **({"Tax exempt": bool(line.tax_exempt)} if tax_exempt else {}),
        }
        for line in lines
    ] or [blank]
    column_config: dict[str, object] = {
        "Category": st.column_config.TextColumn(
            help="Product category, such as Sardines or Yobick.",
        ),
        "Quantity": st.column_config.NumberColumn(
            min_value=0.0,
            step=1.0,
            format="%.2f",
        ),
        "Amount": st.column_config.NumberColumn(
            min_value=0.0,
            step=100.0,
            format="%.2f",
            help="This category's share of the invoice.",
        ),
    }
    if tax_exempt:
        column_config["Tax exempt"] = st.column_config.CheckboxColumn(
            help="Skip the ex-tax delivery step for this amount.",
        )
    edited = st.data_editor(
        pd.DataFrame(starting),
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        key=key,
        column_config=column_config,
    )
    parsed: list[InvoiceCategory] = []
    for row in edited.to_dict("records"):
        name = row.get("Category")
        if name is None or (isinstance(name, float) and pd.isna(name)):
            name = ""
        quantity = row.get("Quantity")
        amount = row.get("Amount")
        if quantity is None or (isinstance(quantity, float) and pd.isna(quantity)):
            quantity = 0.0
        if amount is None or (isinstance(amount, float) and pd.isna(amount)):
            amount = 0.0
        exempt = bool(row.get("Tax exempt")) if tax_exempt else False
        parsed.append(
            InvoiceCategory(
                category=str(name),
                quantity=float(quantity),
                amount=float(amount),
                tax_exempt=exempt,
            )
        )
    return parsed


def _render_city_summary(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    st.caption(
        "Category view shows money by category and sub-category. "
        "Item view shows pieces for each product on its own row."
    )
    _export_excel(repo, supermarket, shop, key=f"export_summary_{supermarket}_{shop}")
    amounts, missing = repo.city_category_summary(supermarket, shop)
    st.markdown("**By category**")
    if amounts:
        st.dataframe(
            pd.DataFrame(amounts),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Total amount": st.column_config.NumberColumn(format="%,.2f"),
            },
        )
    else:
        st.info(f"No category amounts for {shop} yet.")
    if missing:
        st.caption(f"{missing} invoice(s) have no product lines.")
    items = repo.city_item_summary(supermarket, shop)
    st.markdown("**By product**")
    if not items:
        st.info(f"No product quantities for {shop} yet.")
        return
    st.dataframe(
        pd.DataFrame(items),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Quantity (pieces)": st.column_config.NumberColumn(format="%,.2f"),
            "Amount": st.column_config.NumberColumn(format="%,.2f"),
        },
    )


def _fmt_qty(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _render_category_summary(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    _render_city_summary(repo, supermarket, shop)


def _makro_rate_inputs(
    key_prefix: str, *, tax_pct: float, retro_pct: float
) -> tuple[float, float]:
    """Makro tax and retro as percents. Returns both as fractions."""
    st.caption(
        "Enter the tax and retro percents for this invoice. "
        "5 means 5%. Use 0 when that charge does not apply."
    )
    tax_col, retro_col = st.columns(2)
    tax = tax_col.number_input(
        "Tax %",
        min_value=0.0,
        max_value=100.0,
        value=float(tax_pct),
        step=0.5,
        format="%.2f",
        key=f"{key_prefix}_makro_tax",
        help="Added only to the taxable share, after the discount.",
    )
    retro = retro_col.number_input(
        "Retro %",
        min_value=0.0,
        max_value=100.0,
        value=float(retro_pct),
        step=0.5,
        format="%.2f",
        key=f"{key_prefix}_makro_retro",
        help="Taken off the grand total after tax.",
    )
    return tax / 100.0, retro / 100.0


def _fmt_money(value: object) -> str:
    return f"{float(value):,.2f}"  # type: ignore[arg-type]


def _show_totals(items: list[tuple[str, str]]) -> None:
    """Full amounts at body size. Metric widgets clip long numbers."""
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.markdown(f"**{label}**  \n{value}")


def _display_records(
    rows: list[dict[str, object]], supermarket: str
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            "Invoice": row["Invoice No."],
            "Total": _fmt_money(row["Total Invoice Amount"]),
            "Expected": _fmt_money(row["Expected Final Payment"]),
            "Received": _fmt_money(row["Actual Received Amount"]),
            "Variance": _fmt_money(row["Variance"]),
            "Status": row["Status"],
        }
        if supermarket == MAKRO:
            record["Tax %"] = row.get("Tax %", "")
            record["Retro %"] = row.get("Retro %", "")
            record["Received date"] = row.get("Received Date", "")
            record["Cut-off"] = row.get("Retro cut-off", "")
        records.append(record)
    return records


def _show_report_table(rows: list[dict[str, object]], supermarket: str) -> None:
    st.dataframe(
        pd.DataFrame(_display_records(rows, supermarket)),
        use_container_width=True,
        hide_index=True,
    )


def _rate_inputs(
    invoice: SupermarketInvoice,
    *,
    capital_retro_pct: float = 3.0,
) -> tuple[float, float, float, date | None]:
    """Return retro, delivery, and tax as fractions, plus the Makro cut-off."""
    supermarket = invoice.supermarket_name
    cut_off: date | None = None
    if supermarket == CAPITAL:
        options = [0.0, 4.0, 6.0]
        shop_pct = capital_delivery_rate(invoice.shop) * 100
        st.caption(
            "Retro usually starts at 3% and can be changed, including for Hyper. "
            f"Delivery is applied when this payment is recorded. {invoice.shop} "
            f"uses {shop_pct:g}% "
            "(Hyper 0%, DC MDY 6%, CREL Distribution Center and G&G 4%). "
            "When tax is included, the delivery amount is calculated on the "
            "ex-tax base and then subtracted from the invoice balance."
        )
        retro_col, delivery_col = st.columns(2)
        retro_pct = retro_col.number_input(
            "Retro %",
            min_value=0.0,
            max_value=100.0,
            value=float(capital_retro_pct),
            step=0.5,
            format="%.2f",
            key=f"recon_capital_retro_{invoice.id}",
            help="Taken off the invoice total before delivery. 3 means 3%.",
        )
        delivery_pct = delivery_col.selectbox(
            "Delivery charge %",
            options=options,
            index=options.index(shop_pct) if shop_pct in options else 1,
            format_func=lambda n: f"{n:g}%",
            key=f"recon_capital_dc_{invoice.id}",
        )
        return retro_pct / 100.0, delivery_pct / 100.0, 0.0, None
    if supermarket == MAKRO:
        tax_rate, retro_rate = _makro_rate_inputs(
            f"pay_{invoice.id}",
            tax_pct=invoice.tax_percentage * 100,
            retro_pct=invoice.retro_percentage * 100,
        )
        use_cut_off = st.checkbox(
            "Record a retro cut-off date", key="recon_makro_cutoff_on"
        )
        if use_cut_off:
            cut_off = st.date_input(
                "Retro cut-off date", value=utc_today(), key="recon_makro_cutoff"
            )
        return retro_rate, 0.0, tax_rate, cut_off
    c1, c2 = st.columns(2)
    retro_pct = c1.number_input(
        "Back margin %",
        min_value=0.0,
        max_value=100.0,
        step=0.5,
        format="%.2f",
        key="recon_osm_retro",
        help="One Stop Mart retro, entered as a percent. 3 means 3%.",
    )
    delivery_pct = c2.number_input(
        "Delivery incentive %",
        min_value=0.0,
        max_value=100.0,
        step=0.5,
        format="%.2f",
        key="recon_osm_dc",
    )
    if supermarket != ONE_STOP_MART:
        raise ValueError(f"Unknown supermarket: {supermarket}")
    return retro_pct / 100.0, delivery_pct / 100.0, 0.0, None


def _render_report(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> None:
    rows = repo.report_rows(supermarket, shop)
    _export_excel(repo, supermarket, shop, key=f"export_report_{supermarket}_{shop}")
    if not rows:
        st.info(f"No invoices to reconcile for {shop} yet.")
        return

    total_invoice = money(sum(float(r["Total Invoice Amount"]) for r in rows))
    total_expected = money(sum(float(r["Expected Final Payment"]) for r in rows))
    total_actual = money(sum(float(r["Actual Received Amount"]) for r in rows))
    total_variance = money(total_actual - total_expected)
    _show_totals(
        [
            ("Invoices", str(len(rows))),
            ("Expected", _fmt_money(total_expected)),
            ("Received", _fmt_money(total_actual)),
            ("Variance", _fmt_money(total_variance)),
        ]
    )
    st.caption(f"Original invoice totals: {_fmt_money(total_invoice)}")
    _show_report_table(rows, supermarket)
    st.caption(
        "Variance is actual received minus expected. "
        "A negative number is a shortage; a positive number is an overpayment."
    )
    st.markdown("**How the expected amount was calculated**")
    for row in rows:
        with st.expander(
            f"{row['Invoice No.']} · {row['Supermarket']} · {row['Shop']}"
        ):
            st.dataframe(
                pd.DataFrame(
                    [{"Step": label, "Amount": value} for label, value in row["steps"]]
                ),
                use_container_width=True,
                hide_index=True,
            )


def _export_excel(
    repo: ReconciliationRepository, supermarket: str, shop: str, *, key: str
) -> None:
    """Download this shop's invoices, payments, and summaries. Nothing is imported."""
    st.download_button(
        "Export Excel",
        data=reconciliation_workbook_bytes(repo, supermarket, shop),
        file_name=export_filename(supermarket, shop),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key,
    )
