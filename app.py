"""Streamlit entry point for BNA Data Entry."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from bnadataentry.calculations import (
    balance_to_receive_amount,
    dc_applies_at_payment,
    suggest_capital_dc,
)
from bnadataentry.clients import get_client
from bnadataentry.db import DEFAULT_DB_PATH, get_connection, init_db
from bnadataentry.excel_export import client_workbook_bytes
from bnadataentry.models import Payment
from bnadataentry.payments import (
    PAYMENT_METHODS,
    STATUS_LABELS,
    STATUS_OVERPAID,
    STATUS_PAID,
    STATUS_PARTIAL,
    STATUS_UNPAID,
    ar_snapshot,
    utc_today,
)
from bnadataentry.recon_ui import render_reconciliation
from bnadataentry.repository import InvoiceRepository
from bnadataentry.ui_helpers import (
    apply_line_totals,
    browse_line_rows,
    build_invoice_from_form,
    client_template_blurb,
    detail_sheet_columns,
    invoice_lines_for_editor,
    line_totals_footer,
    pack_qty_editor_labels,
    preview_invoice_totals,
    summary_columns,
    summary_sheet_rows,
)
from bnadataentry.validation import normalize_invoice_no

st.set_page_config(
    page_title="BNA Data Entry",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _fmt_money(value: float | None) -> str:
    return f"{float(value or 0):,.2f}"


def _render_money_table(items: list[tuple[str, float]]) -> None:
    """Show full money amounts in a table (st.metric truncates long values)."""
    st.dataframe(
        pd.DataFrame([{name: _fmt_money(value) for name, value in items}]),
        use_container_width=True,
        hide_index=True,
    )


def _default_dates(base) -> tuple[date, date, date]:
    """Invoice / PO / Due defaults: today, today, today+30 (or from base)."""
    today = utc_today()
    if base is not None:
        inv = base.invoice_date or today
        po = base.po_date or inv
        due = base.due_date or (inv + timedelta(days=30))
        return inv, po, due
    return today, today, today + timedelta(days=30)


def _goto_edit_invoice(invoice_id: int) -> None:
    """Navigate to Invoices → Edit for a specific invoice id."""
    st.session_state.nav_page = "Invoices"
    st.session_state.invoice_action_mode = "Edit Invoice"
    st.session_state.edit_invoice_id = int(invoice_id)
    st.rerun()


def _friendly_invoice_conflict(
    *,
    invoice_no: str,
    branch: str,
    invoice_date: date | None,
    existing_id: int | None = None,
) -> str:
    date_bit = (
        invoice_date.isoformat() if isinstance(invoice_date, date) else "unknown date"
    )
    msg = (
        f"Invoice **{invoice_no}** already exists for **{branch}** on **{date_bit}**."
    )
    if existing_id is not None:
        msg += " Open **Edit Invoice** instead of adding again."
    return msg


@st.cache_resource
def _ensure_schema() -> str:
    """Initialize schema once; return DB path (do not cache Connection)."""
    path = str(DEFAULT_DB_PATH)
    conn = init_db(path)
    conn.close()
    return path


def get_repo() -> InvoiceRepository:
    # Fresh connection per Streamlit rerun avoids cross-thread SQLite errors.
    path = _ensure_schema()
    return InvoiceRepository(get_connection(path))


def main() -> None:
    st.title("Invoice reconciliation")
    st.caption("Enter an invoice, record the payment, then check expected against received.")

    repo = get_repo()
    render_reconciliation(repo.conn)


def render_browse(
    repo: InvoiceRepository,
    client_id: str,
    year_from: int,
    year_to: int,
    month: int | None,
    *,
    branch: str | None = None,
) -> None:
    """Invoice-centric Browse: header → product lines only → summary footer."""
    client = get_client(client_id)
    label = branch or "All branches"
    year_label = (
        str(year_from) if year_from == year_to else f"{year_from}–{year_to}"
    )
    st.subheader(f"Browse — {client.name} / {label} / {year_label}")
    st.caption(client_template_blurb(client_id))
    st.caption(
        "Each invoice: header → product lines only → calculated summary "
        "(no Excel Total rows in the grid)."
    )
    invoices = repo.list_invoices(
        client_id=client_id,
        branch=branch,
        year_from=year_from,
        year_to=year_to,
        month=month,
    )
    invoices = [inv for inv in invoices if inv.lines]
    paid_map = repo.sum_paid_by_invoice_ids(
        [inv.id for inv in invoices if inv.id is not None]
    )
    st.write(
        f"{len(invoices)} invoice(s) · "
        f"{sum(len(inv.lines) for inv in invoices)} product line(s)"
    )
    if not invoices:
        st.info("No invoices yet. Import Excel files or add a new invoice.")
        return

    invoice_nos = sorted({inv.invoice_no for inv in invoices if inv.invoice_no})
    filter_no = st.selectbox(
        "Filter by Invoice No",
        options=["All"] + invoice_nos,
    )
    if filter_no != "All":
        invoices = [inv for inv in invoices if inv.invoice_no == filter_no]

    ordered = sorted(
        invoices,
        key=lambda inv: (
            inv.branch or "",
            inv.invoice_date.isoformat() if inv.invoice_date else "",
            inv.invoice_no or "",
            inv.id or 0,
        ),
    )
    expand_all = len(ordered) <= 3

    for inv in ordered:
        inv_date = inv.invoice_date.isoformat() if inv.invoice_date else "—"
        snap = ar_snapshot(
            inv, amount_paid=paid_map.get(inv.id or 0, 0.0)
        )
        status_bit = snap["status_label"]
        if snap["is_overdue"]:
            status_bit = f"{status_bit} · Overdue"
        title = (
            f"Invoice {inv.invoice_no}  ·  {inv.branch}  ·  {inv_date}"
            f"  ·  {status_bit}"
        )
        with st.expander(title, expanded=expand_all):
            # --- 1. Header (client-specific detail-sheet fields) ---
            st.markdown("##### Invoice header")
            if client_id == "makro":
                c1, c2, c3, c4 = st.columns(4)
                c1.markdown(f"**Invoice No**  \n{inv.invoice_no}")
                c2.markdown(
                    f"**Po Date**  \n"
                    f"{inv.po_date.isoformat() if inv.po_date else '—'}"
                )
                c3.markdown(f"**Delivery Date**  \n{inv_date}")
                c4.markdown(
                    f"**Due Date (Payment)**  \n"
                    f"{inv.due_date.isoformat() if inv.due_date else '—'}"
                )
                b1, b2, b3 = st.columns(3)
                b1.markdown(f"**Client**  \n{client.name}")
                b2.markdown(f"**Branch**  \n{inv.branch or '—'}")
                b3.markdown(f"**Remark**  \n{inv.summary.remark or '—'}")
            else:
                c1, c2, c3, c4 = st.columns(4)
                c1.markdown(f"**Invoice No**  \n{inv.invoice_no}")
                c2.markdown(f"**Invoice Date**  \n{inv_date}")
                c3.markdown(f"**Client**  \n{client.name}")
                c4.markdown(f"**Branch**  \n{inv.branch or '—'}")
                p1, p2, p3 = st.columns(3)
                p1.markdown(f"**PO No**  \n{inv.po_no or '—'}")
                p2.markdown(
                    f"**PO Date**  \n"
                    f"{inv.po_date.isoformat() if inv.po_date else '—'}"
                )
                p3.markdown(
                    f"**Due Date**  \n"
                    f"{inv.due_date.isoformat() if inv.due_date else '—'}"
                )

            # --- 2. Product lines only ---
            st.markdown("##### Line items")
            st.dataframe(
                pd.DataFrame(browse_line_rows(inv, client_id)),
                use_container_width=True,
                hide_index=True,
            )

            # --- 3. Summary footer ---
            st.markdown("##### Summary")
            totals = preview_invoice_totals(inv)
            summary_items: list[tuple[str, float]] = [
                ("Total Amount", totals["merchandise_total"]),
                (
                    "Tax 5%" if client_id == "makro" else "Discount",
                    totals["tax_total"]
                    if client_id == "makro"
                    else totals["discount_total"],
                ),
                ("Grand Total", totals["grand_total"]),
                ("Tax", totals["tax_total"]),
                ("Retro", totals["retro"]),
            ]
            if dc_applies_at_payment(client_id):
                summary_items.append(
                    ("Invoice Balance (after Retro)", totals["balance_to_receive"])
                )
                st.caption(
                    "Capital **Delivery Charge** is applied on the **Payments** page."
                )
            else:
                summary_items.extend(
                    [
                        ("Delivery Charge", totals["delivery_charge"]),
                        ("Balance (To Receive)", totals["balance_to_receive"]),
                    ]
                )
            _render_money_table(summary_items)

            # --- 4. Collections ---
            st.markdown("##### Collections")
            collection_items: list[tuple[str, float]] = []
            if snap.get("dc_at_payment"):
                collection_items.extend(
                    [
                        ("Invoice Balance", float(snap["invoice_balance"])),
                        ("Delivery Charge", float(snap["delivery_charge"])),
                    ]
                )
            collection_items.extend(
                [
                    ("Amount Due", snap["amount_due"]),
                    ("Paid", snap["amount_paid"]),
                    ("Outstanding", snap["outstanding"]),
                ]
            )
            _render_money_table(collection_items)
            st.caption(
                f"Status: **{snap['status_label']}**"
                + (" · Overdue" if snap["is_overdue"] else "")
            )
            if inv.id is not None and st.button(
                "Edit this invoice",
                key=f"browse_edit_{inv.id}",
            ):
                _goto_edit_invoice(inv.id)


def render_summary(
    repo: InvoiceRepository,
    client_id: str,
    year_from: int,
    year_to: int,
    month: int | None,
    *,
    branch: str | None = None,
) -> None:
    """Excel Summary sheet view — rows come from the workbook Summary sheet."""
    client = get_client(client_id)
    year_label = (
        str(year_from) if year_from == year_to else f"{year_from}–{year_to}"
    )
    filter_bits = [f"Summary - {client.name}"]
    if branch:
        filter_bits.append(branch)
    else:
        filter_bits.append("All branches")
    filter_bits.append(year_label)
    if month is not None:
        filter_bits.append(date(2000, month, 1).strftime("%b"))

    st.subheader(" · ".join(filter_bits))
    st.caption(
        f"Summary for **{client.name}** "
        f"({len(summary_columns(client_id))} columns), built from saved invoices. "
        "Sidebar **Branch** / **From–To year** / **Month** filter these rows."
    )

    invoices = repo.list_invoices(
        client_id=client_id,
        branch=branch,
        year_from=year_from,
        year_to=year_to,
        month=month,
    )
    rows = summary_sheet_rows(invoices, client_id)

    st.write(f"{len(rows)} invoice row(s) on this summary")
    if not rows:
        st.info(
            "No summary rows match the sidebar filters. "
            "Widen Branch / year range / Month, or check the Excel file in the data folder."
        )
        return

    cols = list(summary_columns(client_id))
    display_df = pd.DataFrame(rows).reindex(columns=cols)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # Footer totals for the filtered Excel summary rows
    if client_id == "capital":
        bal = sum(float(r.get("Balance (To Receive)") or 0) for r in rows)
        retro = sum(float(r.get("Retro (3%)") or 0) for r in rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Invoices", len(rows))
        c2.metric("Total Retro (3%)", f"{retro:,.2f}")
        c3.metric("Total Balance (To Receive)", f"{bal:,.2f}")
    elif client_id == "makro":
        amount = sum(float(r.get("Amount") or 0) for r in rows)
        tax = sum(float(r.get("Tax 5%") or 0) for r in rows)
        grand = sum(float(r.get("Grand Total") or 0) for r in rows)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Invoices", len(rows))
        c2.metric("Total Tax 5%", f"{tax:,.2f}")
        c3.metric("Total Grand Total", f"{grand:,.2f}")
        c4.metric("Total Amount", f"{amount:,.2f}")
    else:
        bal = sum(float(r.get("Balance (To Receive)") or 0) for r in rows)
        merchandise = sum(float(r.get("Total Invoice") or 0) for r in rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Invoices", len(rows))
        c2.metric("Total Invoice", f"{merchandise:,.2f}")
        c3.metric("Total Balance (To Receive)", f"{bal:,.2f}")


def render_edit(
    repo: InvoiceRepository,
    client,
    *,
    branch: str | None = None,
    year_from: int = 2025,
    year_to: int = 2026,
    month: int | None = None,
) -> None:
    """Add / Edit invoices — scoped to sidebar Client / Branch / year range / Month."""
    year_label = (
        str(year_from) if year_from == year_to else f"{year_from}–{year_to}"
    )
    filter_bits = [client.name]
    if branch:
        filter_bits.append(branch)
    else:
        filter_bits.append("All branches")
    filter_bits.append(year_label)
    if month is not None:
        filter_bits.append(date(2000, month, 1).strftime("%b"))

    st.subheader(f"Invoices — {' · '.join(filter_bits)}")
    st.caption(client_template_blurb(client.id))
    st.caption(
        "Sidebar **Client** / **Branch** / **From–To year** / **Month** control this page. "
        "Add locks Client (+ Branch when selected). "
        "Edit lists invoices matching those filters (Browse can deep-link here)."
    )

    action = st.radio(
        "Action",
        ["Add New Invoice", "Edit Invoice"],
        horizontal=True,
        key="invoice_action_mode",
    )

    if action == "Add New Invoice":
        flash_key = f"add_flash_{client.id}"
        if flash_key in st.session_state:
            st.success(st.session_state.pop(flash_key))
        st.markdown(
            f"Create a **new** **{client.name}** invoice"
            + (f" for branch **{branch}**." if branch else ".")
            + " Dates default to today / due +30 days; form clears after a successful save."
        )
        if not branch and len(client.branches) > 1:
            st.info(
                "Sidebar Branch is **All** — pick a branch on the form, "
                "or set Branch in the sidebar to lock it."
            )
        nonce_key = f"add_form_nonce_{client.id}_{branch or 'all'}"
        if nonce_key not in st.session_state:
            st.session_state[nonce_key] = 0
        _render_invoice_form(
            repo,
            client,
            base=None,
            sidebar_branch=branch,
            mode="add",
            key_prefix=(
                f"add_{client.id}_{branch or 'all'}_"
                f"{st.session_state[nonce_key]}"
            ),
            form_nonce_key=nonce_key,
        )
        return

    # ----- Edit Invoice -----
    st.markdown(
        "Pick a row below (or open **Edit this invoice** from Browse), then update it."
    )
    existing = [
        inv
        for inv in repo.list_invoices(
            client_id=client.id,
            branch=branch,
            year_from=year_from,
            year_to=year_to,
            month=month,
        )
        if inv.id
    ]
    deep_id = st.session_state.get("edit_invoice_id")
    deep_invoice = None
    if deep_id is not None:
        deep_invoice = repo.get_invoice(int(deep_id))
        if deep_invoice is None:
            st.session_state.pop("edit_invoice_id", None)
            deep_id = None
        elif deep_invoice.client_id != client.id:
            st.warning(
                f"Deep-linked invoice belongs to **{deep_invoice.client_id}** — "
                "switch the sidebar Client to edit it."
            )
            deep_invoice = None
            deep_id = None

    if not existing and deep_invoice is None:
        st.info(
            f"No invoices for **{' · '.join(filter_bits)}**. "
            "Widen year range / Month / Branch in the sidebar, or use **Add New Invoice**."
        )
        return

    search = st.text_input(
        "Search Invoice No / Branch",
        value="",
        placeholder="Type part of an invoice number or branch…",
        key=(
            f"edit_search_{client.id}_{branch or 'all'}_"
            f"{year_from}_{year_to}_{month}"
        ),
    )
    filtered = existing
    if search.strip():
        q = search.strip().lower()
        filtered = [
            inv
            for inv in existing
            if q in (inv.invoice_no or "").lower()
            or q in (inv.branch or "").lower()
        ]

    if (
        deep_invoice is not None
        and deep_invoice.id is not None
        and all(inv.id != deep_invoice.id for inv in filtered)
    ):
        filtered = [deep_invoice, *filtered]
        st.info(
            f"Showing deep-linked invoice "
            f"**[{deep_invoice.branch}] {deep_invoice.invoice_no}** "
            "(outside current filters)."
        )

    if not filtered:
        st.warning("No invoices match that search within the current filters.")
        return

    paid_map = repo.sum_paid_by_invoice_ids(
        [inv.id for inv in filtered if inv.id is not None]
    )
    sorted_invs = sorted(
        filtered,
        key=lambda i: (
            i.invoice_date.isoformat() if i.invoice_date else "",
            i.invoice_no or "",
        ),
        reverse=True,
    )
    picker_rows: list[dict[str, Any]] = []
    for inv in sorted_invs:
        assert inv.id is not None
        snap = ar_snapshot(inv, amount_paid=paid_map.get(inv.id, 0.0))
        status = snap["status_label"]
        if snap["is_overdue"]:
            status = f"{status} · Overdue"
        picker_rows.append(
            {
                "id": inv.id,
                "Branch": inv.branch or "",
                "Invoice No": inv.invoice_no,
                "Date": inv.invoice_date.isoformat() if inv.invoice_date else "",
                "Lines": len(inv.lines),
                "Balance": round(float(snap["amount_due"]), 2),
                "Outstanding": round(float(snap["outstanding"]), 2),
                "Status": status,
            }
        )
    picker_df = pd.DataFrame(picker_rows)
    st.write(f"{len(picker_rows)} invoice(s)")
    table_key = (
        f"edit_table_{client.id}_{branch or 'all'}_"
        f"{year_from}_{year_to}_{month}"
    )
    selection = st.dataframe(
        picker_df.drop(columns=["id"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=table_key,
    )
    id_options = [int(r["id"]) for r in picker_rows]
    selected_rows = list(getattr(selection.selection, "rows", []) or [])
    if selected_rows:
        st.session_state.edit_invoice_id = int(
            picker_df.iloc[int(selected_rows[0])]["id"]
        )
    target_id = st.session_state.get("edit_invoice_id")
    if target_id is None or int(target_id) not in id_options:
        st.session_state.edit_invoice_id = id_options[0]
        target_id = id_options[0]

    base = repo.get_invoice(int(target_id))
    if base is None:
        st.error("Invoice not found.")
        st.session_state.pop("edit_invoice_id", None)
        return

    st.markdown(
        f"**Editing:** [{base.branch}] **{base.invoice_no}** · "
        f"{base.invoice_date} — click another row in the table to switch."
    )
    _render_invoice_form(
        repo,
        client,
        base=base,
        sidebar_branch=branch,
        mode="edit",
        key_prefix=f"edit_{client.id}_{base.id}",
    )


def _pick_branch(
    *,
    client,
    base,
    sidebar_branch: str | None,
    mode: str,
    key_prefix: str,
    label: str = "Branch",
    help_text: str | None = None,
) -> str:
    """Branch field: locked on Edit / when sidebar Branch is set; else selectable."""
    branch_options = list(client.branches)
    if mode == "edit" and base and base.branch:
        st.markdown(f"**{label}** (locked)  \n{base.branch}")
        return base.branch

    if sidebar_branch and sidebar_branch in branch_options:
        st.markdown(f"**{label}** (from sidebar)  \n{sidebar_branch}")
        return sidebar_branch

    if base and base.branch in branch_options:
        preferred = base.branch
    else:
        preferred = branch_options[0]
    return st.selectbox(
        label,
        options=branch_options,
        index=branch_options.index(preferred),
        key=f"{key_prefix}_branch",
        help=help_text,
    )


def _render_invoice_form(
    repo: InvoiceRepository,
    client,
    *,
    base,
    sidebar_branch: str | None,
    mode: str,
    key_prefix: str,
    form_nonce_key: str | None = None,
) -> None:
    """
    Shared invoice-centric form for Add (INSERT) and Edit (UPDATE + replace lines).

    Client always comes from the sidebar. Branch follows sidebar when set.
    Layout: 1) Header  2) Line items only  3) Delivery Charge + Summary
    """
    is_makro = client.id == "makro"
    is_capital = client.id == "capital"
    is_osm = client.id == "one_stop_mart"
    is_edit = mode == "edit"
    default_inv_date, default_po_date, default_due_date = _default_dates(base)

    default_retro_pct = (
        float(base.summary.retro_pct_override)
        if base and base.summary.retro_pct_override is not None
        else float(client.default_retro_pct)
    )
    default_tax_pct = (
        float(base.summary.tax_pct_override)
        if base and base.summary.tax_pct_override is not None
        else float(client.default_tax_pct)
    )

    # ----- 1. Invoice header -----
    st.markdown("### 1. Invoice header")
    if is_makro:
        # Makro detail sheet: Po Date | Delivery Date | Due Date | Invoice No
        h1, h2, h3, h4 = st.columns(4)
        if is_edit and base:
            h1.markdown(f"**Invoice No** (locked)  \n{base.invoice_no}")
            invoice_no = base.invoice_no
        else:
            invoice_no = h1.text_input(
                "Invoice No",
                value="",
                key=f"{key_prefix}_invoice_no",
            )
        po_date = h2.date_input(
            "Po Date",
            value=default_po_date,
            key=f"{key_prefix}_po_date",
        )
        invoice_date = h3.date_input(
            "Delivery Date",
            value=default_inv_date,
            key=f"{key_prefix}_invoice_date",
        )
        due_date = h4.date_input(
            "Due Date (Payment)",
            value=default_due_date,
            key=f"{key_prefix}_due_date",
        )
        po_no = ""
        b1, b2, b3 = st.columns(3)
        b1.markdown(f"**Client** (from sidebar)  \n{client.name}")
        with b2:
            branch = _pick_branch(
                client=client,
                base=base,
                sidebar_branch=sidebar_branch,
                mode=mode,
                key_prefix=key_prefix,
                label="Branch (sheet)",
                help_text=(
                    "Makro-Sat Sun → Sat San · "
                    "Makro - Kha Wei Chan → Kha Wae Chan"
                ),
            )
        remark = b3.text_input(
            "Remark",
            value=base.summary.remark if base else "",
            key=f"{key_prefix}_remark",
        )
        product_label = st.text_input(
            "Product (summary label)",
            value=base.product_label if base else "",
            key=f"{key_prefix}_product_label",
            placeholder="Short name for Summary - Makro",
        )
    else:
        h1, h2, h3, h4 = st.columns(4)
        if is_edit and base:
            h1.markdown(f"**Invoice No** (locked)  \n{base.invoice_no}")
            invoice_no = base.invoice_no
        else:
            invoice_no = h1.text_input(
                "Invoice No",
                value="",
                key=f"{key_prefix}_invoice_no",
            )
        invoice_date = h2.date_input(
            "Invoice Date",
            value=default_inv_date,
            key=f"{key_prefix}_invoice_date",
        )
        h3.markdown(f"**Client** (from sidebar)  \n{client.name}")
        with h4:
            branch = _pick_branch(
                client=client,
                base=base,
                sidebar_branch=sidebar_branch,
                mode=mode,
                key_prefix=key_prefix,
                label="Branch",
            )

        if is_osm:
            po_no = ""
            d1, d2 = st.columns(2)
            po_date = d1.date_input(
                "PO Date",
                value=default_po_date,
                key=f"{key_prefix}_po_date",
            )
            due_date = d2.date_input(
                "Due Date (Payment)",
                value=default_due_date,
                key=f"{key_prefix}_due_date",
            )
        else:
            d1, d2, d3 = st.columns(3)
            po_no = d1.text_input(
                "PO No",
                value=base.po_no if base else "",
                key=f"{key_prefix}_po_no",
            )
            po_date = d2.date_input(
                "PO Date",
                value=default_po_date,
                key=f"{key_prefix}_po_date",
            )
            due_date = d3.date_input(
                "Due Date (Payment)",
                value=default_due_date,
                key=f"{key_prefix}_due_date",
            )

    with st.expander(
        "Rates & summary overrides (optional)",
        expanded=False,
    ):
        if not is_makro:
            product_label = st.text_input(
                "Product (summary label)",
                value=base.product_label if base else "",
                key=f"{key_prefix}_product_label",
            )
            remark = st.text_input(
                "Remark",
                value=base.summary.remark if base else "",
                key=f"{key_prefix}_remark",
            )
        if is_makro:
            st.caption(
                "Tax 5% is on the Makro **detail** sheet. "
                "Retro (−5%) is applied on **Summary - Makro** only."
            )
            r1, r2 = st.columns(2)
            tax_pct_override = r1.number_input(
                "Tax 5% (rate)",
                min_value=0.0,
                max_value=100.0,
                value=default_tax_pct,
                key=f"{key_prefix}_tax_pct",
            )
            retro_pct_override = r2.number_input(
                "Retro (−5%) rate (Summary)",
                min_value=0.0,
                max_value=100.0,
                value=default_retro_pct,
                key=f"{key_prefix}_retro_pct",
            )
            discount_pct = 0.0
        elif is_capital:
            tax_pct_override = 0.0
            retro_pct_override = st.number_input(
                "Retro (3%) rate",
                min_value=0.0,
                max_value=100.0,
                value=default_retro_pct,
                key=f"{key_prefix}_retro_pct",
            )
            discount_pct = retro_pct_override
        else:
            tax_pct_override = 0.0
            retro_pct_override = st.number_input(
                "Retro / Discount (5%,10%) rate",
                min_value=0.0,
                max_value=100.0,
                value=default_retro_pct,
                key=f"{key_prefix}_retro_pct",
            )
            discount_pct = retro_pct_override

        use_manual = st.checkbox(
            "Manually edit summary amounts",
            key=f"{key_prefix}_use_manual",
        )
        if is_makro:
            m1, m2, m3 = st.columns(3)
            tax_amount = m1.number_input(
                "Tax amount",
                value=float(base.summary.tax_amount or 0) if base else 0.0,
                disabled=not use_manual,
                key=f"{key_prefix}_tax_amt",
            )
            retro_amount = m2.number_input(
                "Retro amount (usually negative)",
                value=float(base.summary.retro_amount or 0) if base else 0.0,
                disabled=not use_manual,
                key=f"{key_prefix}_retro_amt",
            )
            balance_to_receive = m3.number_input(
                "Amount (Summary)",
                value=float(base.summary.balance_to_receive or 0) if base else 0.0,
                disabled=not use_manual,
                key=f"{key_prefix}_balance",
            )
        else:
            tax_amount = 0.0
            m1, m2 = st.columns(2)
            retro_amount = m1.number_input(
                "Retro amount (usually negative)",
                value=float(base.summary.retro_amount or 0) if base else 0.0,
                disabled=not use_manual,
                key=f"{key_prefix}_retro_amt",
            )
            balance_to_receive = m2.number_input(
                "Balance (To Receive)",
                value=float(base.summary.balance_to_receive or 0) if base else 0.0,
                disabled=not use_manual,
                key=f"{key_prefix}_balance",
            )

    # ----- 2. Line items ONLY -----
    st.markdown("### 2. Line items")
    qty_labels = pack_qty_editor_labels(client.id)
    if is_makro:
        line_hint = (
            "Makro columns: Product · Pack Qty **ဖာ | Pcs** · List Price · "
            "**Total Invoice** · **Tax 5%** · **Grand Total**. "
            "Tick FOC when Excel shows FOC in Total Invoice / Grand Total."
        )
    elif is_capital:
        line_hint = "Pack Qty = **Pk** | **Unit**. Total = Pk × List Price."
    else:
        line_hint = "Pack Qty = **Pk (pcs)**. Total = Qty × List Price."
    st.caption(
        f"{line_hint} No **Total Amount** footer row in the grid — "
        "summary metrics below calculate automatically."
    )

    seed = apply_line_totals(
        invoice_lines_for_editor(base, client.id),
        client_id=client.id,
        discount_pct=discount_pct,
        tax_pct=tax_pct_override if is_makro else 0.0,
    )
    if not seed:
        seed = invoice_lines_for_editor(base, client.id)

    col_cfg: dict[str, Any] = {
        "product_description": st.column_config.TextColumn(
            "Product Description", width="large", required=True
        ),
        "pack_qty": st.column_config.NumberColumn(
            qty_labels["pack_qty"],
            min_value=0.0,
        ),
        "list_price": st.column_config.NumberColumn("List Price", min_value=0.0),
        "line_total": st.column_config.NumberColumn(
            "Total Invoice" if is_makro else "Total (Qty × Price)",
            disabled=True,
            help="Auto: Pcs × List Price" if is_makro else "Auto: Qty × List Price",
        ),
    }
    if is_makro:
        col_cfg["pack_qty_fa"] = st.column_config.NumberColumn(
            qty_labels["pack_qty_fa"], min_value=0.0
        )
        col_cfg["tax_amount"] = st.column_config.NumberColumn(
            "Tax 5%",
            disabled=True,
            help="Auto from Tax 5% rate (leave 0 on line to auto-calc)",
        )
        col_cfg["grand_total"] = st.column_config.NumberColumn(
            "Grand Total",
            disabled=True,
            help="Auto: Total Invoice + Tax 5%",
        )
        col_cfg["is_foc"] = st.column_config.CheckboxColumn(
            "FOC",
            help="When checked, Total Invoice / Grand Total are FOC (like Excel)",
        )
    elif is_capital:
        col_cfg["pack_qty_fa"] = st.column_config.NumberColumn(
            qty_labels["pack_qty_fa"], min_value=0.0
        )

    lines_df = st.data_editor(
        pd.DataFrame(seed),
        num_rows="dynamic",
        use_container_width=True,
        column_config=col_cfg,
        column_order=detail_sheet_columns(client.id),
        hide_index=True,
        key=f"{key_prefix}_lines",
    )

    prepared = apply_line_totals(
        lines_df.to_dict(orient="records"),
        client_id=client.id,
        discount_pct=discount_pct,
        tax_pct=tax_pct_override if is_makro else 0.0,
    )
    footer = line_totals_footer(prepared)

    # Live AR preview: Total + Tax + Retro − Delivery Charge = Balance
    merch = float(footer.get("merchandise_total") or 0.0)
    live_tax = float(tax_amount) if use_manual and is_makro else float(
        footer.get("tax_total") or 0.0
    )
    if is_makro and not use_manual and live_tax == 0.0 and tax_pct_override:
        live_tax = round(merch * float(tax_pct_override) / 100.0, 2)
    if use_manual:
        live_retro = float(retro_amount)
    else:
        live_retro = -float(footer.get("discount_total") or 0.0)
        if live_retro == 0.0 and discount_pct:
            base_for_retro = merch + (live_tax if is_makro else 0.0)
            live_retro = round(-base_for_retro * float(discount_pct) / 100.0, 2)

    # ----- 3. Summary (Capital DC is applied on Payments) -----
    st.markdown("### 3. Summary")
    capital_dc_deferred = dc_applies_at_payment(client.id)
    if capital_dc_deferred:
        st.caption(
            "Invoice Balance = Total − |Retro|. "
            "**Delivery Charge** (0% / 4% / 6%) is set on the **Payments** page."
        )
        delivery_charge = (
            float(base.summary.delivery_charge or 0) if base else 0.0
        )
        live_dc = 0.0  # not part of invoice balance
    else:
        st.caption("Balance = Total + Tax − |Retro| − Delivery Charge")
        default_dc = float(base.summary.delivery_charge or 0) if base else 0.0
        delivery_charge = st.number_input(
            "Delivery Charge (deduction)",
            min_value=0.0,
            value=default_dc,
            step=100.0,
            format="%.2f",
            key=f"{key_prefix}_delivery_charge",
            help="Positive amount deducted from balance.",
        )
        live_dc = abs(float(delivery_charge or 0.0))

    live_balance = (
        float(balance_to_receive)
        if use_manual
        else balance_to_receive_amount(
            merch, live_tax, live_retro, 0.0 if capital_dc_deferred else live_dc
        )
    )
    live_totals = {
        **footer,
        "tax_total": live_tax,
        "retro": live_retro,
        "delivery_charge": abs(float(delivery_charge or 0.0)),
        "balance_to_receive": live_balance,
    }

    money_rows: list[tuple[str, float]] = [
        ("Total Amount", float(live_totals.get("merchandise_total") or 0)),
        (
            "Tax 5%"
            if is_makro
            else ("Discount 5%, 3%" if is_capital else "Discount (5%,10%)"),
            float(
                live_totals.get("tax_total")
                if is_makro
                else live_totals.get("discount_total")
                or 0
            ),
        ),
        ("Grand Total", float(live_totals.get("grand_total") or 0)),
        ("Tax", float(live_totals.get("tax_total") or 0)),
        ("Retro", float(live_totals.get("retro") or 0)),
    ]
    if capital_dc_deferred:
        money_rows.append(
            ("Invoice Balance (after Retro)", float(live_balance))
        )
        suggested = (
            suggest_capital_dc(float(live_balance), branch) if branch else 0.0
        )
        if suggested > 0:
            st.info(
                f"Suggested Delivery Charge for **{branch}** at payment: "
                f"**{suggested:,.2f}** "
                f"(formula `(Balance/105)×100 × rate%`)."
            )
    else:
        money_rows.extend(
            [
                ("Delivery Charge", float(live_dc)),
                ("Balance (To Receive)", float(live_balance)),
            ]
        )
    _render_money_table(money_rows)

    button_label = "Update invoice" if is_edit else "Save new invoice"
    if st.button(button_label, type="primary", key=f"{key_prefix}_submit"):
        norm_no = normalize_invoice_no(invoice_no)
        if not norm_no:
            st.error("Invoice No is required.")
            return
        if not branch:
            st.error("Branch is required.")
            return
        inv_date = invoice_date if isinstance(invoice_date, date) else None
        conflict = repo.find_by_key(
            client.id, norm_no, inv_date, branch=branch
        )
        if conflict is not None and (not is_edit or conflict.id != getattr(base, "id", None)):
            st.error(
                _friendly_invoice_conflict(
                    invoice_no=norm_no,
                    branch=branch,
                    invoice_date=inv_date,
                    existing_id=conflict.id,
                )
            )
            return

        payload: dict[str, Any] = {
            "client_id": client.id,
            "branch": branch,
            "invoice_no": norm_no,
            "invoice_date": invoice_date,
            "po_no": po_no,
            "po_date": po_date,
            "due_date": due_date,
            "product_label": product_label,
            "remark": remark,
            "discount_pct": discount_pct,
            "tax_pct_override": tax_pct_override,
            "retro_pct_override": retro_pct_override,
            "use_manual_summary": use_manual,
            # Capital: keep DC set on Payments; invoice save must not wipe it.
            "delivery_charge": (
                abs(float(delivery_charge or 0.0))
                if capital_dc_deferred
                else live_dc
            ),
            "lines": prepared,
        }
        if use_manual:
            payload["tax_amount"] = tax_amount
            payload["retro_amount"] = retro_amount
            payload["balance_to_receive"] = balance_to_receive
        try:
            invoice = build_invoice_from_form(payload)
            if is_edit:
                if not base or not base.id:
                    raise ValueError("No invoice selected to update")
                invoice.id = base.id
                # update_invoice deletes old line_items then inserts new rows
                repo.update_invoice(invoice, recompute=False)
                action_word = "Updated"
            else:
                repo.create_invoice(invoice, recompute=False)
                action_word = "Created"
            totals = preview_invoice_totals(invoice)
            msg = (
                f"{action_word} [{invoice.branch}] invoice {invoice.invoice_no} — "
                f"{len(invoice.lines)} product line(s). "
                f"Balance={totals['balance_to_receive']:,.2f} "
                f"(DC={totals['delivery_charge']:,.2f})."
            )
            if not is_edit and form_nonce_key is not None:
                st.session_state[f"add_flash_{client.id}"] = msg
                st.session_state[form_nonce_key] = (
                    int(st.session_state.get(form_nonce_key, 0)) + 1
                )
                st.rerun()
            st.success(msg)
        except sqlite3.IntegrityError:
            st.error(
                _friendly_invoice_conflict(
                    invoice_no=norm_no,
                    branch=branch,
                    invoice_date=inv_date,
                    existing_id=None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    if is_edit and base and base.id is not None:
        st.markdown("---")
        with st.expander("Delete invoice", expanded=False):
            st.warning(
                "Deletes this invoice, its line items, and any recorded payments. "
                "This cannot be undone."
            )
            confirm = st.checkbox(
                f"Yes, delete [{base.branch}] {base.invoice_no}",
                key=f"{key_prefix}_delete_confirm",
            )
            if st.button(
                "Delete invoice permanently",
                type="secondary",
                disabled=not confirm,
                key=f"{key_prefix}_delete_btn",
            ):
                try:
                    repo.delete_invoice(int(base.id))
                    st.session_state.pop("edit_invoice_id", None)
                    st.success(
                        f"Deleted [{base.branch}] invoice {base.invoice_no}."
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))


def render_payments(
    repo: InvoiceRepository,
    client_id: str,
    year_from: int,
    year_to: int,
    month: int | None,
    *,
    branch: str | None = None,
) -> None:
    """AR list + record / edit / delete payments for filtered invoices."""
    client = get_client(client_id)
    year_label = (
        str(year_from) if year_from == year_to else f"{year_from}–{year_to}"
    )
    label = branch or "All branches"
    st.subheader(f"Payments — {client.name} · {label} · {year_label}")
    if dc_applies_at_payment(client_id):
        st.caption(
            "Capital: set **Delivery Charge** here "
            "(Hyper 0% · other 4% · DC MDY 6%, base `(Balance/105)×100`). "
            "**Amount Due** = Invoice Balance − DC. Then record collections."
        )
    else:
        st.caption(
            "Record collections against invoices. "
            "**Amount Due** comes from the invoice balance; payments do not change it."
        )

    invoices = [
        inv
        for inv in repo.list_invoices(
            client_id=client_id,
            branch=branch,
            year_from=year_from,
            year_to=year_to,
            month=month,
        )
        if inv.id is not None
    ]
    if not invoices:
        st.info("No invoices in this filter. Import or widen From–To year / Branch.")
        return

    paid_map = repo.sum_paid_by_invoice_ids([inv.id for inv in invoices if inv.id])
    rows: list[dict[str, Any]] = []
    snapshots: dict[int, dict[str, Any]] = {}
    for inv in invoices:
        assert inv.id is not None
        snap = ar_snapshot(inv, amount_paid=paid_map.get(inv.id, 0.0))
        snapshots[inv.id] = snap
        display_status = snap["status_label"]
        if snap["is_overdue"]:
            display_status = f"{display_status} · Overdue"
        rows.append(
            {
                "id": inv.id,
                "Branch": inv.branch or "",
                "Invoice No": inv.invoice_no,
                "Invoice Date": (
                    inv.invoice_date.isoformat() if inv.invoice_date else ""
                ),
                "Due Date": inv.due_date.isoformat() if inv.due_date else "",
                "Amount Due": snap["amount_due"],
                "Paid": snap["amount_paid"],
                "Outstanding": snap["outstanding"],
                "Status": display_status,
                "_status": snap["status"],
                "_overdue": snap["is_overdue"],
            }
        )

    status_filter = st.selectbox(
        "Status filter",
        options=[
            "All",
            "Unpaid",
            "Partial",
            "Paid",
            "Overpaid",
            "Overdue",
        ],
        help="Overdue = past due date with outstanding balance.",
    )
    filtered = rows
    if status_filter == "Unpaid":
        filtered = [r for r in rows if r["_status"] == STATUS_UNPAID]
    elif status_filter == "Partial":
        filtered = [r for r in rows if r["_status"] == STATUS_PARTIAL]
    elif status_filter == "Paid":
        filtered = [r for r in rows if r["_status"] == STATUS_PAID]
    elif status_filter == "Overpaid":
        filtered = [r for r in rows if r["_status"] == STATUS_OVERPAID]
    elif status_filter == "Overdue":
        filtered = [r for r in rows if r["_overdue"]]

    total_due = sum(r["Amount Due"] for r in filtered)
    total_paid = sum(r["Paid"] for r in filtered)
    total_out = sum(r["Outstanding"] for r in filtered)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Invoices", len(filtered))
    m2.metric("Total Due", f"{total_due:,.2f}")
    m3.metric("Total Paid", f"{total_paid:,.2f}")
    m4.metric("Outstanding", f"{total_out:,.2f}")

    display_cols = [
        "Branch",
        "Invoice No",
        "Invoice Date",
        "Due Date",
        "Amount Due",
        "Paid",
        "Outstanding",
        "Status",
    ]
    if dc_applies_at_payment(client_id):
        for r in filtered:
            inv_row = next(
                (i for i in invoices if i.id == r["id"]), None
            )
            if inv_row is not None:
                r["Invoice Balance"] = snapshots[int(r["id"])]["invoice_balance"]
                r["DC"] = snapshots[int(r["id"])]["delivery_charge"]
        display_cols = [
            "Branch",
            "Invoice No",
            "Invoice Date",
            "Due Date",
            "Invoice Balance",
            "DC",
            "Amount Due",
            "Paid",
            "Outstanding",
            "Status",
        ]
    st.dataframe(
        pd.DataFrame(filtered).reindex(columns=display_cols) if filtered else pd.DataFrame(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )
    if not filtered:
        st.info("No invoices match this status filter.")
        return

    by_id = {inv.id: inv for inv in invoices if inv.id is not None}
    label_to_id = {
        (
            f"[{r['Branch']}] {r['Invoice No']}  ·  {r['Invoice Date']}  ·  "
            f"out={r['Outstanding']:,.2f}  ·  {r['Status']}"
        ): int(r["id"])
        for r in filtered
    }
    selected_label = st.selectbox(
        "Select invoice",
        options=list(label_to_id.keys()),
        key=f"pay_pick_{client_id}_{branch or 'all'}_{year_from}_{year_to}_{month}",
    )
    inv_id = label_to_id[selected_label]
    inv = by_id[inv_id]
    snap = snapshots[inv_id]

    st.markdown(
        f"##### Invoice {inv.invoice_no} · {inv.branch} — "
        f"{STATUS_LABELS.get(snap['status'], snap['status'])}"
        + (" · Overdue" if snap["is_overdue"] else "")
    )

    if dc_applies_at_payment(client_id):
        invoice_balance = float(snap["invoice_balance"])
        suggested_dc = suggest_capital_dc(invoice_balance, inv.branch or "")
        current_dc = abs(float(inv.summary.delivery_charge or 0.0))
        st.markdown("###### Delivery Charge (payment stage)")
        d1, d2, d3 = st.columns(3)
        d1.metric("Invoice Balance", f"{invoice_balance:,.2f}")
        new_dc = d2.number_input(
            "Delivery Charge",
            min_value=0.0,
            value=float(current_dc if current_dc > 0 else suggested_dc),
            step=100.0,
            format="%.2f",
            key=f"pay_dc_{inv_id}",
            help=(
                f"Suggested for {inv.branch or 'branch'}: {suggested_dc:,.2f} "
                "(Hyper 0%, DC MDY 6%, other 4%)."
            ),
        )
        net_due = round(invoice_balance - abs(float(new_dc)), 2)
        d3.metric("Amount Due (after DC)", f"{net_due:,.2f}")
        if suggested_dc > 0:
            st.caption(
                f"Suggested DC for **{inv.branch}**: **{suggested_dc:,.2f}** "
                f"= `(({invoice_balance:,.2f}/105)×100) × rate%`."
            )
        if st.button("Save Delivery Charge", key=f"pay_dc_save_{inv_id}"):
            try:
                repo.update_summary_overrides(
                    inv_id,
                    delivery_charge=abs(float(new_dc)),
                    recompute=False,
                )
                # Keep invoice balance (pre-DC); only persist DC.
                loaded = repo.get_invoice(inv_id)
                if loaded is not None:
                    loaded.summary.delivery_charge = abs(float(new_dc))
                    # Ensure balance stays pre-DC if a prior recompute touched it
                    if loaded.summary.balance_to_receive is None:
                        merch = sum(line.line_total or 0 for line in loaded.lines)
                        loaded.summary.balance_to_receive = balance_to_receive_amount(
                            merch,
                            loaded.summary.tax_amount or 0.0,
                            loaded.summary.retro_amount or 0.0,
                            0.0,
                        )
                        repo.update_invoice(loaded, recompute=False)
                st.success(
                    f"Saved Delivery Charge {abs(float(new_dc)):,.2f}. "
                    f"Amount Due = {net_due:,.2f}."
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        # Refresh snap after potential DC edit display
        snap = ar_snapshot(
            inv,
            amount_paid=paid_map.get(inv_id, 0.0),
        )
        # Use saved DC for metrics below (widget may differ until save)
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Invoice Balance", f"{snap['invoice_balance']:,.2f}")
        s2.metric("Delivery Charge", f"{snap['delivery_charge']:,.2f}")
        s3.metric("Amount Due", f"{snap['amount_due']:,.2f}")
        s4.metric("Outstanding", f"{snap['outstanding']:,.2f}")
    else:
        s1, s2, s3 = st.columns(3)
        s1.metric("Amount Due", f"{snap['amount_due']:,.2f}")
        s2.metric("Paid", f"{snap['amount_paid']:,.2f}")
        s3.metric("Outstanding", f"{snap['outstanding']:,.2f}")

    payments = repo.list_payments(inv_id)
    st.markdown("###### Payment history")
    if payments:
        hist = pd.DataFrame(
            [
                {
                    "Date": p.payment_date.isoformat(),
                    "Amount": p.amount,
                    "Method": p.method,
                    "Reference": p.reference,
                    "Remark": p.remark,
                    "id": p.id,
                }
                for p in payments
            ]
        )
        st.dataframe(
            hist.drop(columns=["id"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No payments recorded yet.")

    tab_add, tab_edit = st.tabs(["Record payment", "Edit / delete payment"])

    with tab_add:
        default_amt = max(snap["outstanding"], 0.0)
        with st.form(f"pay_add_{inv_id}"):
            c1, c2 = st.columns(2)
            pay_date = c1.date_input("Payment date", value=utc_today())
            amount = c2.number_input(
                "Amount",
                min_value=0.01,
                value=float(default_amt) if default_amt > 0 else 0.01,
                step=100.0,
                format="%.2f",
            )
            method = st.selectbox(
                "Method",
                options=list(PAYMENT_METHODS),
                format_func=lambda m: m.title(),
            )
            reference = st.text_input("Reference (cheque / slip / bank ref)", value="")
            remark = st.text_input("Remark", value="")
            submitted = st.form_submit_button("Save payment", type="primary")
        if submitted:
            try:
                repo.create_payment(
                    Payment(
                        invoice_id=inv_id,
                        payment_date=pay_date
                        if isinstance(pay_date, date)
                        else date.fromisoformat(str(pay_date)),
                        amount=float(amount),
                        method=str(method),
                        reference=reference.strip(),
                        remark=remark.strip(),
                    )
                )
                st.success(
                    f"Recorded payment of {float(amount):,.2f} for invoice {inv.invoice_no}."
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    with tab_edit:
        if not payments:
            st.caption("Nothing to edit yet.")
        else:
            pay_labels = {
                f"{p.payment_date.isoformat()} · {p.amount:,.2f} · {p.method or '—'} "
                f"· {p.reference or 'no ref'}": p
                for p in payments
                if p.id is not None
            }
            pick = st.selectbox(
                "Payment to edit",
                options=list(pay_labels.keys()),
                key=f"pay_edit_pick_{inv_id}",
            )
            selected = pay_labels[pick]
            with st.form(f"pay_edit_{selected.id}"):
                c1, c2 = st.columns(2)
                pay_date = c1.date_input(
                    "Payment date", value=selected.payment_date
                )
                amount = c2.number_input(
                    "Amount",
                    min_value=0.01,
                    value=float(selected.amount),
                    step=100.0,
                    format="%.2f",
                )
                method_opts = list(PAYMENT_METHODS)
                method_idx = (
                    method_opts.index(selected.method)
                    if selected.method in method_opts
                    else 0
                )
                method = st.selectbox(
                    "Method",
                    options=method_opts,
                    index=method_idx,
                    format_func=lambda m: m.title(),
                )
                reference = st.text_input(
                    "Reference", value=selected.reference or ""
                )
                remark = st.text_input("Remark", value=selected.remark or "")
                col_save, col_del = st.columns(2)
                save = col_save.form_submit_button("Update payment", type="primary")
                delete = col_del.form_submit_button("Delete payment")
            if save:
                try:
                    selected.payment_date = (
                        pay_date
                        if isinstance(pay_date, date)
                        else date.fromisoformat(str(pay_date))
                    )
                    selected.amount = float(amount)
                    selected.method = str(method)
                    selected.reference = reference.strip()
                    selected.remark = remark.strip()
                    repo.update_payment(selected)
                    st.success("Payment updated.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
            if delete:
                try:
                    assert selected.id is not None
                    repo.delete_payment(selected.id)
                    st.success("Payment deleted.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))


def render_import_export(
    repo: InvoiceRepository,
    client_id: str,
    year_from: int,
    year_to: int,
    month: int | None,
) -> None:
    """Download the saved invoices for this client. Excel import is not offered."""
    st.subheader("Export")
    client = get_client(client_id)
    year_label = (
        str(year_from) if year_from == year_to else f"{year_from}–{year_to}"
    )
    period = year_label
    if month is not None:
        period = f"{period} / {date(2000, month, 1).strftime('%B')}"
    st.caption(
        f"Download an Excel workbook for **{client.name}** using **{period}**."
    )
    st.download_button(
        "Export Excel",
        data=client_workbook_bytes(
            repo,
            client_id,
            year_from=year_from,
            year_to=year_to,
            month=month,
        ),
        file_name=f"{client.name}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="btn_download_export",
    )


if __name__ == "__main__":
    main()
