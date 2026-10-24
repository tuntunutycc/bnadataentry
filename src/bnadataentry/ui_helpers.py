"""Pure helpers that map UI form payloads into validated domain objects."""

from __future__ import annotations

from datetime import date
from typing import Any

from bnadataentry.calculations import (
    balance_to_receive_amount,
    compute_line_totals,
    dc_applies_at_payment,
)
from bnadataentry.clients import get_client
from bnadataentry.models import Invoice, InvoiceSummary, LineItem
from bnadataentry.validation import (
    is_foc_value,
    is_total_row,
    normalize_invoice_no,
    parse_date,
    parse_number,
    require_non_empty,
)


def client_template_blurb(client_id: str) -> str:
    """Short caption describing which Excel detail template is active."""
    if client_id == "capital":
        return (
            "Capital template: PO No · Pack Qty **Pk | Unit** · "
            "Discount / Retro **3%** (no Tax, no FOC)."
        )
    if client_id == "makro":
        return (
            "Makro detail sheet: **Po Date** · **Delivery Date** · Due Date · "
            "Invoice No · Product · Pack Qty **ဖာ | Pcs** · List Price · "
            "**Total Invoice** · **Tax 5%** · **Grand Total** · Remark "
            "(no PO No; Retro is on Summary only)."
        )
    return (
        "One Stop Mart template: no PO No · Pack Qty **Pk (pcs)** · "
        "Discount / Retro **5% or 10%** (no Tax, no FOC)."
    )


def pack_qty_editor_labels(client_id: str) -> dict[str, str]:
    """Human labels for pack_qty / pack_qty_fa matching each Excel workbook."""
    if client_id == "makro":
        return {"pack_qty_fa": "Pack Qty (ဖာ)", "pack_qty": "Pack Qty (Pcs)"}
    if client_id == "capital":
        return {"pack_qty": "Pack Qty (Pk)", "pack_qty_fa": "Pack Qty (Unit)"}
    return {"pack_qty": "Pack Qty (Pk (pcs))"}


def detail_sheet_columns(client_id: str) -> list[str]:
    """Editable product columns for invoice-centric data_editor (no Total footer rows)."""
    if client_id == "makro":
        # Matches Makro branch sheets (Sat San / Kha Wei Chan)
        return [
            "product_description",
            "pack_qty_fa",  # ဖာ
            "pack_qty",  # Pcs
            "list_price",
            "line_total",  # Total Invoice
            "tax_amount",  # Tax 5%
            "grand_total",
            "is_foc",
        ]
    if client_id == "capital":
        return [
            "product_description",
            "pack_qty",
            "pack_qty_fa",
            "list_price",
            "line_total",
        ]
    # One Stop Mart — single Pack Qty (Pk (pcs)); no Unit / Tax / FOC
    return [
        "product_description",
        "pack_qty",
        "list_price",
        "line_total",
    ]


def empty_detail_line(client_id: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "product_description": "",
        "pack_qty": 0.0,
        "list_price": 0.0,
        "discount_amount": 0.0,
        "line_total": 0.0,
    }
    if client_id == "makro":
        row["pack_qty_fa"] = 0.0
        row["tax_amount"] = 0.0
        row["grand_total"] = 0.0
        row["is_foc"] = False
    elif client_id == "capital":
        row["pack_qty_fa"] = 0.0  # Pack Qty (Unit)
    return row


def invoice_lines_for_editor(invoice: Invoice | None, client_id: str) -> list[dict[str, Any]]:
    if not invoice or not invoice.lines:
        return [empty_detail_line(client_id)]
    rows: list[dict[str, Any]] = []
    for line in invoice.lines:
        row: dict[str, Any] = {
            "product_description": line.product_description,
            "pack_qty": line.pack_qty,
            "list_price": line.list_price,
            "discount_amount": line.discount_amount,
            "line_total": float(line.line_total or 0.0),
        }
        if client_id == "makro":
            row["pack_qty_fa"] = line.pack_qty_fa
            row["tax_amount"] = float(line.tax_amount or 0.0)
            row["grand_total"] = float(line.grand_total or 0.0)
            row["is_foc"] = line.is_foc
        elif client_id == "capital":
            row["pack_qty_fa"] = line.pack_qty_fa  # Unit
        rows.append(row)
    return rows


def filter_product_line_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Excel-style Total / Grand Total footer rows — never save as line items."""
    kept: list[dict[str, Any]] = []
    for raw in rows:
        product = str(raw.get("product_description") or "").strip()
        po = str(raw.get("po_no") or raw.get("invoice_no") or "").strip()
        if is_total_row(product) or is_total_row(po):
            continue
        if not product:
            continue
        kept.append(raw)
    return kept


def apply_line_totals(
    rows: list[dict[str, Any]],
    *,
    client_id: str,
    discount_pct: float | None = None,
    tax_pct: float | None = None,
) -> list[dict[str, Any]]:
    """
    Fill Total / Discount / Grand Total like the Excel detail sheets.

    Total = Pack Qty × List Price
    If discount is 0/blank and discount_pct is set → Discount = Total × pct/100
    Grand Total = Total − Discount (+ Tax for Makro)

    Subtotal / "Total" labeled rows are ignored (not stored as products).
    """
    client = get_client(client_id)
    if discount_pct is None:
        discount_pct = client.default_retro_pct if client_id != "makro" else 0.0
    if tax_pct is None:
        tax_pct = client.default_tax_pct

    prepared: list[dict[str, Any]] = []
    for raw in filter_product_line_rows(rows):
        product = str(raw.get("product_description") or "").strip()
        foc = bool(raw.get("is_foc")) or is_foc_value(raw.get("foc"))
        qty = parse_number(raw.get("pack_qty"), allow_foc=True)
        qty_fa = parse_number(raw.get("pack_qty_fa"), allow_foc=True)
        price = parse_number(raw.get("list_price"), allow_foc=True)
        total = 0.0 if foc else round(qty * price, 6)
        discount = parse_number(raw.get("discount_amount"), allow_foc=True)
        if not foc and discount == 0.0 and discount_pct and total:
            discount = round(total * float(discount_pct) / 100.0, 6)
        tax = parse_number(raw.get("tax_amount"), allow_foc=True)
        if client_id == "makro" and not foc and tax == 0.0 and tax_pct and total:
            tax = round(total * float(tax_pct) / 100.0, 6)
        if client_id != "makro":
            tax = 0.0
        grand = 0.0 if foc else round(total - discount + tax, 6)
        prepared.append(
            {
                "product_description": product,
                "pack_qty": qty,
                "pack_qty_fa": qty_fa,
                "list_price": price,
                "line_total": total,
                "discount_amount": discount,
                "tax_amount": tax,
                "grand_total": grand,
                "is_foc": foc,
            }
        )
    return prepared


def line_totals_footer(prepared_lines: list[dict[str, Any]]) -> dict[str, float]:
    """Sum of computed line amounts for UI footer (not a DB row)."""
    return {
        "merchandise_total": round(
            sum(float(r.get("line_total") or 0) for r in prepared_lines), 2
        ),
        "discount_total": round(
            sum(float(r.get("discount_amount") or 0) for r in prepared_lines), 2
        ),
        "tax_total": round(
            sum(float(r.get("tax_amount") or 0) for r in prepared_lines), 2
        ),
        "grand_total": round(
            sum(float(r.get("grand_total") or 0) for r in prepared_lines), 2
        ),
    }


def build_invoice_from_form(payload: dict[str, Any]) -> Invoice:
    """
    Build an Invoice from a branch-sheet-style form payload.

    Expected keys match detail sheets:
      client_id, branch, invoice_no, invoice_date, po_no, po_date, due_date,
      remark, discount_pct (optional auto), tax_pct_override / retro_pct_override,
      lines: product_description, pack_qty, list_price, discount_amount, ...
    """
    client_id = require_non_empty(payload.get("client_id"), "client_id")
    client = get_client(client_id)
    invoice_no = normalize_invoice_no(
        require_non_empty(payload.get("invoice_no"), "invoice_no")
    )
    branch = str(payload.get("branch") or "").strip()
    if not branch and client.branches:
        branch = client.branches[0]

    invoice_date = _as_date(payload.get("invoice_date"), "invoice_date")
    po_date = _as_date(payload.get("po_date"), "po_date", required=False)
    due_date = _as_date(payload.get("due_date"), "due_date", required=False)

    discount_pct = payload.get("discount_pct")
    if discount_pct in (None, ""):
        discount_pct = None
    else:
        discount_pct = float(discount_pct)

    tax_pct = payload.get("tax_pct_override")
    if tax_pct in (None, ""):
        tax_pct = client.default_tax_pct
    else:
        tax_pct = float(tax_pct)

    prepared = apply_line_totals(
        list(payload.get("lines") or []),
        client_id=client_id,
        discount_pct=discount_pct,
        tax_pct=tax_pct,
    )
    if not prepared:
        raise ValueError("At least one line item is required")

    lines = [
        LineItem(
            product_description=row["product_description"],
            pack_qty=row["pack_qty"],
            pack_qty_fa=row.get("pack_qty_fa", 0.0),
            list_price=row["list_price"],
            line_total=row["line_total"],
            discount_amount=row["discount_amount"],
            tax_amount=row["tax_amount"],
            grand_total=row["grand_total"],
            is_foc=row["is_foc"],
        )
        for row in prepared
    ]
    for line in lines:
        compute_line_totals(line)

    merchandise = round(sum(line.line_total or 0.0 for line in lines), 6)
    discount_sum = round(sum(line.discount_amount for line in lines), 6)
    tax_sum = round(sum(line.tax_amount for line in lines), 6)

    summary = InvoiceSummary(
        remark=str(payload.get("remark") or ""),
        branch_amounts={branch: merchandise} if branch else {},
    )
    if payload.get("tax_pct_override") not in (None, ""):
        summary.tax_pct_override = float(payload["tax_pct_override"])
    if payload.get("retro_pct_override") not in (None, ""):
        summary.retro_pct_override = float(payload["retro_pct_override"])
    elif discount_pct is not None:
        summary.retro_pct_override = float(discount_pct)

    # Detail-derived defaults; optional absolute summary overrides (1C).
    computed_tax = tax_sum
    if client_id == "makro" and tax_sum == 0.0 and tax_pct:
        computed_tax = round(merchandise * tax_pct / 100.0, 6)

    if client_id in {"capital", "one_stop_mart"}:
        computed_retro = -discount_sum
        if discount_sum == 0.0 and (summary.retro_pct_override or discount_pct):
            pct = summary.retro_pct_override
            if pct is None:
                pct = float(discount_pct or client.default_retro_pct)
            computed_retro = round(-merchandise * pct / 100.0, 6)
    else:
        retro_pct = (
            summary.retro_pct_override
            if summary.retro_pct_override is not None
            else client.default_retro_pct
        )
        computed_retro = round(-(merchandise + computed_tax) * retro_pct / 100.0, 6)

    use_manual = bool(payload.get("use_manual_summary"))
    summary.tax_amount = (
        float(payload["tax_amount"])
        if use_manual and payload.get("tax_amount") not in (None, "")
        else computed_tax
    )
    summary.retro_amount = (
        float(payload["retro_amount"])
        if use_manual and payload.get("retro_amount") not in (None, "")
        else computed_retro
    )
    if payload.get("delivery_charge") not in (None, ""):
        summary.delivery_charge = abs(float(payload["delivery_charge"]))
    else:
        # Capital DC is set on Payments — do not auto-fill on invoice save.
        summary.delivery_charge = 0.0

    if use_manual and payload.get("balance_to_receive") not in (None, ""):
        summary.balance_to_receive = float(payload["balance_to_receive"])
    else:
        dc_in_balance = (
            0.0
            if dc_applies_at_payment(client_id)
            else summary.delivery_charge
        )
        summary.balance_to_receive = balance_to_receive_amount(
            merchandise,
            summary.tax_amount or 0.0,
            summary.retro_amount or 0.0,
            dc_in_balance,
        )
    if payload.get("branch_amounts"):
        summary.branch_amounts = dict(payload["branch_amounts"])

    product_label = str(payload.get("product_label") or "").strip()
    if not product_label:
        product_label = _short_product_label(lines)

    invoice = Invoice(
        client_id=client_id,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        po_no=str(payload.get("po_no") or ""),
        po_date=po_date,
        due_date=due_date,
        branch=branch,
        product_label=product_label,
        lines=lines,
        summary=summary,
    )
    return invoice


def preview_invoice_totals(invoice: Invoice) -> dict[str, float]:
    merchandise = sum(line.line_total or 0.0 for line in invoice.lines)
    discount = sum(line.discount_amount for line in invoice.lines)
    tax = sum(line.tax_amount for line in invoice.lines)
    grand = sum(line.grand_total or 0.0 for line in invoice.lines)
    return {
        "merchandise_total": round(merchandise, 2),
        "discount_total": round(discount, 2),
        "tax_total": round(tax, 2),
        "grand_total": round(grand, 2),
        "retro": round(invoice.summary.retro_amount or 0.0, 2),
        "delivery_charge": round(float(invoice.summary.delivery_charge or 0.0), 2),
        "balance_to_receive": round(invoice.summary.balance_to_receive or 0.0, 2),
    }


def invoice_to_browse_row(invoice: Invoice) -> dict[str, Any]:
    """Backward-compatible generic row; prefer browse_table_rows()."""
    return browse_table_rows([invoice], invoice.client_id)[0]


# Capital detail-sheet general template (union of CREL / DC MDY / G&G / Hyper).
# Invoice No first (unique). Pack Qty: Pk | Unit.
# Multi-line: header on first product row only; Excel-style Total footer after lines.
CAPITAL_BROWSE_COLUMNS: tuple[str, ...] = (
    "Invoice No",
    "Branch",
    "PO No",
    "PO Date",
    "Invoice Date",
    "Due Date (Payment)",
    "Product Description",
    "Pack Qty (Pk)",
    "Pack Qty (Unit)",
    "List Price",
    "Total",
    "Discount 5%, 3%",
    "Grand Total",
)

# Header fields shown only on the first line of a multi-line invoice (Excel style).
_EXCEL_HEADER_BLANK_KEYS: tuple[str, ...] = (
    "Invoice No",
    "PO No",
    "PO Date",
    "Invoice Date",
    "Delivery Date",
    "Due Date (Payment)",
)

# Kept for Makro / OSM until those get their own templates.
BROWSE_DETAIL_COLUMNS: tuple[str, ...] = (
    "Invoice No",
    "Branch",
    "PO No",
    "PO Date",
    "Invoice Date",
    "Due Date (Payment)",
    "Product Description",
    "Pack Qty (Pk / ဖာ)",
    "Pack Qty (Unit / Pcs)",
    "List Price",
    "Total",
    "Discount",
    "Tax",
    "Grand Total",
)


def browse_table_rows(invoices: list[Invoice], client_id: str) -> list[dict[str, Any]]:
    """
    Browse = DETAIL sheet rows (one row per product line), Excel-style.

    Multi-line invoice (same as Capital Excel):
      - First product row: full header (Invoice No, PO, dates, …) + line amounts
      - Following rows: header columns blank; only product / Pack Qty / money filled
      - Final Total row: sums Total / Discount / Grand Total for that invoice

    Sorted by Branch → Invoice Date → Invoice No → line order.
    Summary sheet is never used.
    """
    ordered = sorted(
        invoices,
        key=lambda inv: (
            inv.branch or "",
            inv.invoice_date.isoformat() if inv.invoice_date else "",
            inv.invoice_no or "",
            inv.id or 0,
        ),
    )
    rows: list[dict[str, Any]] = []
    for inv in ordered:
        if not inv.lines:
            continue
        for line_no, line in enumerate(inv.lines, start=1):
            row = _browse_detail_row(inv, client_id, line=line)
            if line_no > 1:
                for key in _EXCEL_HEADER_BLANK_KEYS:
                    if key in row:
                        row[key] = ""
            rows.append(row)
        rows.append(_browse_invoice_total_row(inv, client_id))
    return rows


def _browse_invoice_total_row(inv: Invoice, client_id: str) -> dict[str, Any]:
    """Excel-style Total footer under each invoice's product lines."""
    merchandise = round(sum(line.line_total or 0.0 for line in inv.lines), 2)
    discount = round(sum(line.discount_amount for line in inv.lines), 2)
    tax = round(sum(line.tax_amount for line in inv.lines), 2)
    grand = round(sum(line.grand_total or 0.0 for line in inv.lines), 2)

    if client_id == "capital":
        return {
            "Invoice No": "Total",
            "Branch": inv.branch,
            "PO No": "",
            "PO Date": "",
            "Invoice Date": "",
            "Due Date (Payment)": "",
            "Product Description": "",
            "Pack Qty (Pk)": "",
            "Pack Qty (Unit)": "",
            "List Price": "",
            "Total": merchandise,
            "Discount 5%, 3%": discount,
            "Grand Total": grand,
            "_id": inv.id,
        }
    if client_id == "makro":
        return {
            "Invoice No": "Total",
            "Branch": inv.branch,
            "PO No": "",
            "PO Date": "",
            "Invoice Date": "",
            "Due Date (Payment)": "",
            "Product Description": "",
            "Pack Qty (Pk / ဖာ)": "",
            "Pack Qty (Unit / Pcs)": "",
            "List Price": "",
            "Total": merchandise,
            "Discount": "",
            "Tax": tax,
            "Grand Total": grand,
            "_id": inv.id,
        }
    return {
        "Invoice No": "Total",
        "Branch": inv.branch,
        "PO No": "",
        "PO Date": "",
        "Invoice Date": "",
        "Due Date (Payment)": "",
        "Product Description": "",
        "Pack Qty (Pk / ဖာ)": "",
        "Pack Qty (Unit / Pcs)": "",
        "List Price": "",
        "Total": merchandise,
        "Discount": discount,
        "Tax": "",
        "Grand Total": grand,
        "_id": inv.id,
    }


def _browse_detail_row(
    inv: Invoice, client_id: str, *, line: LineItem | None
) -> dict[str, Any]:
    po_date = inv.po_date.isoformat() if inv.po_date else ""
    inv_date = inv.invoice_date.isoformat() if inv.invoice_date else ""
    due = inv.due_date.isoformat() if inv.due_date else ""
    desc = (line.product_description if line else inv.product_label) or ""

    if client_id == "capital":
        return _capital_browse_row(inv, line, desc, po_date, inv_date, due)

    # --- Makro / OSM (interim shared template) ---
    if line is None:
        pk_or_fa: float | None = None
        unit_or_pcs: float | None = None
        price = total = disc = tax = grand = None
    elif client_id == "makro":
        pk_or_fa = float(line.pack_qty_fa)
        unit_or_pcs = float(line.pack_qty)
        price = float(line.list_price)
        total = float(line.line_total) if line.line_total is not None else 0.0
        disc = None
        tax = float(line.tax_amount)
        grand = float(line.grand_total) if line.grand_total is not None else 0.0
    else:
        pk_or_fa = float(line.pack_qty)
        unit_or_pcs = None
        price = float(line.list_price)
        total = float(line.line_total) if line.line_total is not None else 0.0
        disc = float(line.discount_amount)
        tax = None
        grand = float(line.grand_total) if line.grand_total is not None else 0.0

    return {
        "Invoice No": inv.invoice_no,
        "Branch": inv.branch,
        "PO No": inv.po_no or "",
        "PO Date": po_date,
        "Invoice Date": inv_date,
        "Due Date (Payment)": due,
        "Product Description": desc,
        "Pack Qty (Pk / ဖာ)": pk_or_fa,
        "Pack Qty (Unit / Pcs)": unit_or_pcs,
        "List Price": price,
        "Total": total,
        "Discount": disc,
        "Tax": tax,
        "Grand Total": grand,
        "_id": inv.id,
    }


def _capital_browse_row(
    inv: Invoice,
    line: LineItem | None,
    desc: str,
    po_date: str,
    inv_date: str,
    due: str,
) -> dict[str, Any]:
    """
    Capital general template — every detail-sheet column across all branches.

    Excel Pack Qty spans two columns with sub-headers Pk (or Pk (pcs)) | Unit.
    Unit is stored in pack_qty_fa for Capital (0 / blank when unused).
    """
    if line is None:
        pk = unit = price = total = disc = grand = None
    else:
        pk = float(line.pack_qty)
        # Unit sub-column (usually blank in Excel); 0 → blank cell (not "None")
        unit = float(line.pack_qty_fa) if line.pack_qty_fa else ""
        price = float(line.list_price)
        total = float(line.line_total) if line.line_total is not None else 0.0
        disc = float(line.discount_amount)
        grand = float(line.grand_total) if line.grand_total is not None else 0.0

    return {
        "Invoice No": inv.invoice_no,
        "Branch": inv.branch,
        "PO No": inv.po_no or "",
        "PO Date": po_date,
        "Invoice Date": inv_date,
        "Due Date (Payment)": due,
        "Product Description": desc,
        "Pack Qty (Pk)": pk if pk is not None else "",
        "Pack Qty (Unit)": unit if unit is not None else "",
        "List Price": price if price is not None else "",
        "Total": total if total is not None else "",
        "Discount 5%, 3%": disc if disc is not None else "",
        "Grand Total": grand if grand is not None else "",
        "_id": inv.id,
    }


# Exact Summary-sheet column headers (cleaned from Excel row 1).
CAPITAL_SUMMARY_COLUMNS: tuple[str, ...] = (
    "Po Date",
    "Invoice Date",
    "Due Date",
    "Invoice No",
    "Product",
    "CREL",
    "DC MDY",
    "Hyper",
    "G & G",
    "Retro (3%)",
    # Excel col 11 has no header text; formula = CREL+DC MDY+Hyper+G&G+Retro
    "Subtotal",
    "DC Mdy6%, Other 4%, Hyper 0%",
    "Balance (To Receive)",
    "Remark",
)

MAKRO_SUMMARY_COLUMNS: tuple[str, ...] = (
    "PO Date",
    "Invoice Date",
    "Due Date",
    "Invoice No",
    "Product Description",
    "Sat San",
    "Kha Wae Chan",
    "Tax 5%",
    "Grand Total",
    "Retro ( -5% )",
    "Amount",
    "Remark",
)

OSM_SUMMARY_COLUMNS: tuple[str, ...] = (
    "PO Date",
    "Invoice Date",
    "Due Date",
    "Invoice No",
    "Product",
    "Total Invoice",
    "Retro (5%,10%)",
    "Balance (To Receive)",
)


def summary_columns(client_id: str) -> tuple[str, ...]:
    if client_id == "capital":
        return CAPITAL_SUMMARY_COLUMNS
    if client_id == "makro":
        return MAKRO_SUMMARY_COLUMNS
    return OSM_SUMMARY_COLUMNS


def summary_sheet_rows(invoices: list[Invoice], client_id: str) -> list[dict[str, Any]]:
    """
    One row per invoice — column headers match Excel Summary sheets exactly.
    """
    ordered = sorted(
        invoices,
        key=lambda inv: (
            inv.invoice_date.isoformat() if inv.invoice_date else "",
            inv.invoice_no or "",
            inv.id or 0,
        ),
    )
    rows: list[dict[str, Any]] = []
    for inv in ordered:
        merchandise = round(sum(line.line_total or 0.0 for line in inv.lines), 2)
        ba = dict(inv.summary.branch_amounts or {})
        if inv.branch and inv.branch not in ba and merchandise:
            ba[inv.branch] = merchandise

        product = (inv.product_label or "").strip() or _short_product_label(inv.lines)
        po_date = inv.po_date.isoformat() if inv.po_date else ""
        inv_date = inv.invoice_date.isoformat() if inv.invoice_date else ""
        due = inv.due_date.isoformat() if inv.due_date else ""

        if client_id == "capital":
            crel = float(ba.get("CREL", 0) or 0)
            dc_mdy = float(ba.get("DC MDY", 0) or 0)
            hyper = float(ba.get("Hyper", 0) or 0)
            gng = float(ba.get("G&G", 0) or 0)
            retro = float(inv.summary.retro_amount or 0)
            # Excel col K: =F+G+H+I+J
            subtotal = round(crel + dc_mdy + hyper + gng + retro, 2)
            # Invoice balance = after retro (Subtotal). DC applied at payment.
            invoice_balance = inv.summary.balance_to_receive
            if invoice_balance is None:
                invoice_balance = balance_to_receive_amount(
                    crel + dc_mdy + hyper + gng, 0.0, retro, 0.0
                )
            invoice_balance = float(invoice_balance or 0)
            if not subtotal:
                subtotal = invoice_balance
            stored_dc = float(inv.summary.delivery_charge or 0.0)
            dc_charge = stored_dc  # 0 until set on Payments (Excel may have value)
            excel_balance = round(invoice_balance - abs(dc_charge), 2)
            rows.append(
                {
                    "Po Date": po_date,
                    "Invoice Date": inv_date,
                    "Due Date": due,
                    "Invoice No": inv.invoice_no,
                    "Product": product,
                    "CREL": crel,
                    "DC MDY": dc_mdy,
                    "Hyper": hyper,
                    "G & G": gng,
                    "Retro (3%)": retro,
                    "Subtotal": subtotal,
                    # Excel column shows negative; stored DC is positive magnitude
                    "DC Mdy6%, Other 4%, Hyper 0%": -abs(dc_charge),
                    "Balance (To Receive)": excel_balance,
                    "Remark": inv.summary.remark or "",
                    "_id": inv.id,
                }
            )
        elif client_id == "makro":
            tax = inv.summary.tax_amount
            if tax is None:
                tax = round(sum(line.tax_amount for line in inv.lines), 2)
            tax = float(tax or 0)
            grand = round(sum(line.grand_total or 0.0 for line in inv.lines), 2)
            if not grand:
                grand = round(merchandise + tax, 2)
            rows.append(
                {
                    "PO Date": po_date,
                    "Invoice Date": inv_date,
                    "Due Date": due,
                    "Invoice No": inv.invoice_no,
                    "Product Description": product,
                    "Sat San": float(ba.get("Sat San", 0) or 0),
                    "Kha Wae Chan": float(ba.get("Kha Wae Chan", 0) or 0),
                    "Tax 5%": tax,
                    "Grand Total": grand,
                    "Retro ( -5% )": float(inv.summary.retro_amount or 0),
                    "Amount": float(inv.summary.balance_to_receive or 0),
                    "Remark": inv.summary.remark or "",
                    "_id": inv.id,
                }
            )
        else:
            rows.append(
                {
                    "PO Date": po_date,
                    "Invoice Date": inv_date,
                    "Due Date": due,
                    "Invoice No": inv.invoice_no,
                    "Product": product,
                    "Total Invoice": merchandise,
                    "Retro (5%,10%)": float(inv.summary.retro_amount or 0),
                    "Balance (To Receive)": float(
                        inv.summary.balance_to_receive or 0
                    ),
                    "_id": inv.id,
                }
            )
    return rows


def browse_line_rows(invoice: Invoice, client_id: str) -> list[dict[str, Any]]:
    """
    Product lines only for invoice-centric UI — no Excel Total Amount footer row.
    Column labels follow each client's detail sheet.
    """
    if client_id == "capital":
        return [
            {
                "Product Description": line.product_description,
                "Pack Qty (Pk)": line.pack_qty,
                "Pack Qty (Unit)": line.pack_qty_fa or "",
                "List Price": line.list_price,
                "Total (Qty × Price)": line.line_total,
            }
            for line in invoice.lines
        ]
    if client_id == "makro":
        rows: list[dict[str, Any]] = []
        for line in invoice.lines:
            if line.is_foc:
                total_inv: Any = "FOC"
                tax: Any = ""
                grand: Any = "FOC"
                price: Any = 0
            else:
                total_inv = line.line_total
                tax = line.tax_amount if line.tax_amount else ""
                grand = line.grand_total
                price = line.list_price
            rows.append(
                {
                    "Product Description": line.product_description,
                    "Pack Qty (ဖာ)": line.pack_qty_fa or "",
                    "Pack Qty (Pcs)": line.pack_qty,
                    "List Price": price,
                    "Total Invoice": total_inv,
                    "Tax 5%": tax,
                    "Grand Total": grand,
                }
            )
        return rows
    return [
        {
            "Product Description": line.product_description,
            "Pack Qty (Pk (pcs))": line.pack_qty,
            "List Price": line.list_price,
            "Total (Qty × Price)": line.line_total,
        }
        for line in invoice.lines
    ]


def browse_metric_specs(
    client_id: str, totals: dict[str, float]
) -> list[tuple[str, float]]:
    """Invoice summary footer metrics (aggregates — never a grid row)."""
    dc = float(totals.get("delivery_charge") or 0.0)
    retro = float(totals.get("retro") or 0.0)
    balance = float(totals.get("balance_to_receive") or 0.0)
    if client_id == "makro":
        return [
            ("Total Amount", totals["merchandise_total"]),
            ("Tax 5%", totals["tax_total"]),
            ("Grand Total", totals["grand_total"]),
            ("Retro", retro),
            ("Delivery Charge", dc),
            ("Balance", balance),
        ]
    if client_id == "one_stop_mart":
        return [
            ("Total Amount", totals["merchandise_total"]),
            ("Discount (5%,10%)", totals["discount_total"]),
            ("Grand Total", totals["grand_total"]),
            ("Retro", retro),
            ("Delivery Charge", dc),
            ("Balance", balance),
        ]
    return [
        ("Total Amount", totals["merchandise_total"]),
        ("Discount 5%, 3%", totals["discount_total"]),
        ("Grand Total", totals["grand_total"]),
        ("Retro", retro),
        ("Delivery Charge", dc),
        ("Balance", balance),
    ]


def _short_product_label(lines: list[LineItem]) -> str:
    names = [line.product_description.strip() for line in lines if line.product_description]
    if not names:
        return ""
    if len(names) == 1:
        return names[0][:80]
    return f"{names[0][:40]} +{len(names) - 1} more"


def _as_date(value: Any, field_name: str, *, required: bool = True) -> date | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    if isinstance(value, date):
        return value
    return parse_date(value)
