"""Import client Excel workbooks into SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from bnadataentry.calculations import (
    balance_to_receive_amount,
    dc_applies_at_payment,
    refresh_invoice,
)
from bnadataentry.clients import ClientConfig, get_client
from bnadataentry.models import Invoice, InvoiceSummary, LineItem
from bnadataentry.repository import InvoiceRepository
from bnadataentry.validation import (
    is_foc_value,
    is_total_row,
    normalize_invoice_no,
    parse_date,
    parse_number,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Excel workbooks live under excel_data/; data/ is reserved for SQLite.
EXCEL_DATA_DIR = PROJECT_ROOT / "excel_data"
EXCEL_DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ImportErrorRow:
    sheet: str
    row: int
    message: str


@dataclass
class ImportResult:
    client_id: str
    invoices_upserted: int = 0
    line_items: int = 0
    errors: list[ImportErrorRow] = field(default_factory=list)


def detect_client_from_filename(path: Path) -> str | None:
    name = path.name.lower()
    if "capital" in name:
        return "capital"
    if "makro" in name:
        return "makro"
    if "one stop" in name or "onestop" in name:
        return "one_stop_mart"
    return None


def import_workbook(
    path: Path | str,
    repo: InvoiceRepository,
    *,
    client_id: str | None = None,
) -> ImportResult:
    path = Path(path)
    resolved_client = client_id or detect_client_from_filename(path)
    if not resolved_client:
        raise ValueError(f"Cannot detect client for {path.name}")
    client = get_client(resolved_client)
    result = ImportResult(client_id=resolved_client)

    wb = load_workbook(path, data_only=True)
    try:
        detail_invoices: dict[tuple[str, str, str | None, str], Invoice] = {}
        summary_overlays: list[dict[str, Any]] = []
        for sheet_name in wb.sheetnames:
            if sheet_name.lower().startswith("summary"):
                overlays, s_errors = _parse_summary_sheet(
                    wb[sheet_name], resolved_client
                )
                result.errors.extend(s_errors)
                summary_overlays.extend(overlays)
                continue
            branch = _branch_from_sheet(client, sheet_name)
            ws = wb[sheet_name]
            parsed, errors = _parse_detail_sheet(ws, resolved_client, branch)
            result.errors.extend(errors)
            for inv in parsed:
                key = _invoice_key(inv)
                if key in detail_invoices:
                    existing = detail_invoices[key]
                    existing.lines.extend(inv.lines)
                    if inv.po_no and not existing.po_no:
                        existing.po_no = inv.po_no
                    if inv.po_date and not existing.po_date:
                        existing.po_date = inv.po_date
                    if inv.due_date and not existing.due_date:
                        existing.due_date = inv.due_date
                    if inv.product_label and not existing.product_label:
                        existing.product_label = inv.product_label
                else:
                    detail_invoices[key] = inv

        for inv in detail_invoices.values():
            merchandise = round(
                sum(line.line_total or 0.0 for line in inv.lines), 2
            )
            if inv.branch:
                inv.summary.branch_amounts = {inv.branch: merchandise}
            refresh_invoice(inv, use_stored_amounts=False)

            # Overlay Summary-sheet tax / retro / DC / balance when present
            for overlay in summary_overlays:
                if _summary_overlay_matches(inv, overlay):
                    _apply_summary_overlay(inv, overlay)
                    # Recompute invoice balance unless Summary provided one
                    # (Capital: Excel Balance is net of DC — converted in overlay).
                    if "balance_to_receive" not in overlay:
                        merch = sum(line.line_total or 0.0 for line in inv.lines)
                        dc_in_balance = (
                            0.0
                            if dc_applies_at_payment(inv.client_id)
                            else inv.summary.delivery_charge
                        )
                        inv.summary.balance_to_receive = balance_to_receive_amount(
                            merch,
                            inv.summary.tax_amount or 0.0,
                            inv.summary.retro_amount or 0.0,
                            dc_in_balance,
                        )
                    break

            repo.upsert_invoice(inv, recompute=False)
            result.invoices_upserted += 1
            result.line_items += len(inv.lines)
    finally:
        wb.close()

    return result


def import_all_excel_data(
    repo: InvoiceRepository,
    excel_dir: Path | str | None = None,
) -> list[ImportResult]:
    directory = Path(excel_dir) if excel_dir else EXCEL_DATA_DIR
    results: list[ImportResult] = []
    for path in sorted(directory.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        results.append(import_workbook(path, repo))
    return results


def _safe_upload_filename(name: str) -> str:
    """Keep only the basename; block path traversal from browser uploads."""
    base = Path(name).name.strip() or "upload.xlsx"
    # Collapse weird whitespace; keep Unicode letters used in client names
    cleaned = "".join(ch if ch not in '<>:"|?*\\/' else "_" for ch in base)
    return cleaned or "upload.xlsx"


def import_uploaded_workbooks(
    repo: InvoiceRepository,
    uploads: list[tuple[str, bytes]],
    *,
    staging_dir: Path | str | None = None,
) -> list[ImportResult]:
    """
    Save browser-uploaded workbooks to a staging folder, import each, then
    leave staging files in place (caller may delete the temp dir).

    ``uploads`` is a list of ``(original_filename, file_bytes)``.
    Only ``.xlsx`` is supported (openpyxl). ``.xls`` returns an error result.
    """
    if staging_dir is None:
        staging = EXCEL_DATA_DIR / "uploads"
    else:
        staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    results: list[ImportResult] = []
    for original_name, payload in uploads:
        safe_name = _safe_upload_filename(original_name)
        suffix = Path(safe_name).suffix.lower()
        if suffix == ".xls":
            results.append(
                ImportResult(
                    client_id="unknown",
                    errors=[
                        ImportErrorRow(
                            sheet="(file)",
                            row=0,
                            message=(
                                f"{safe_name}: legacy .xls is not supported. "
                                "Please save/re-export as .xlsx and upload again."
                            ),
                        )
                    ],
                )
            )
            continue
        if suffix != ".xlsx":
            results.append(
                ImportResult(
                    client_id="unknown",
                    errors=[
                        ImportErrorRow(
                            sheet="(file)",
                            row=0,
                            message=f"{safe_name}: expected a .xlsx workbook.",
                        )
                    ],
                )
            )
            continue
        if not detect_client_from_filename(Path(safe_name)):
            results.append(
                ImportResult(
                    client_id="unknown",
                    errors=[
                        ImportErrorRow(
                            sheet="(file)",
                            row=0,
                            message=(
                                f"{safe_name}: cannot detect client. "
                                "Filename should include Capital, Makro, "
                                "or One Stop Mart."
                            ),
                        )
                    ],
                )
            )
            continue

        dest = staging / safe_name
        # Avoid clobbering concurrent uploads with the same name
        if dest.exists():
            stem, ext = dest.stem, dest.suffix
            n = 1
            while True:
                candidate = staging / f"{stem}_{n}{ext}"
                if not candidate.exists():
                    dest = candidate
                    break
                n += 1
        dest.write_bytes(payload)
        try:
            results.append(import_workbook(dest, repo))
        except Exception as exc:  # noqa: BLE001 — surface per-file to the UI
            results.append(
                ImportResult(
                    client_id=detect_client_from_filename(dest) or "unknown",
                    errors=[
                        ImportErrorRow(
                            sheet=dest.name,
                            row=0,
                            message=str(exc),
                        )
                    ],
                )
            )
    return results


def _invoice_key(inv: Invoice) -> tuple[str, str, str | None, str]:
    """Unique per client + invoice + date + branch (detail sheet)."""
    return (
        inv.client_id,
        inv.invoice_no,
        inv.invoice_date.isoformat() if inv.invoice_date else None,
        inv.branch or "",
    )


def _branch_from_sheet(client: ClientConfig, sheet_name: str) -> str:
    cleaned = sheet_name.strip()
    mapping = {
        "crel distribution center": "CREL",
        "dc mdy": "DC MDY",
        "hyper": "Hyper",
        "g&g": "G&G",
        "makro-sat sun": "Sat San",
        "makro - kha wei chan": "Kha Wae Chan",
        "one stop mart": "One Stop Mart",
    }
    key = cleaned.lower().rstrip()
    if key in mapping:
        return mapping[key]
    for branch in client.branches:
        if branch.lower() in key:
            return branch
    return cleaned


def _header_map(ws: Worksheet) -> dict[str, int]:
    """Map normalized header names to 1-based column indexes from row 1."""
    headers: dict[str, int] = {}
    for col in range(1, (ws.max_column or 0) + 1):
        value = ws.cell(1, col).value
        if value is None:
            continue
        norm = _normalize_header(str(value))
        if norm and norm not in headers:
            headers[norm] = col
    return headers


def _normalize_header(text: str) -> str:
    t = " ".join(text.replace("\n", " ").split()).lower()
    replacements = {
        "po date": "po_date",
        "po no": "po_no",
        "po  date": "po_date",
        "delivery date": "delivery_date",
        "invoice date": "invoice_date",
        "due date": "due_date",
        "due date (payment)": "due_date",
        "due date  (payment)": "due_date",
        "invoice no": "invoice_no",
        "product": "product",
        "product description": "product",
        "pack qty": "pack_qty",
        "list price": "list_price",
        "total": "total",
        "total invoice": "total",
        "discount 5%, 3%": "discount",
        "discount (5%,10%)": "discount",
        "grand total": "grand_total",
        "tax 5%": "tax",
        "remark": "remark",
        "crel": "crel",
        "dc mdy": "dc_mdy",
        "hyper": "hyper",
        "g & g": "g_and_g",
        "sat san": "sat_san",
        "kha wae chan": "kha_wae_chan",
        "retro (3%)": "retro",
        "retro ( -5% )": "retro",
        "retro (5%,10%)": "retro",
        "balance (to receive)": "balance",
        "amount": "amount",
    }
    # Try exact then fuzzy
    for raw, key in replacements.items():
        if t == raw or t.rstrip() == raw:
            return key
    if "due date" in t:
        return "due_date"
    if "invoice date" in t:
        return "invoice_date"
    if "invoice no" in t:
        return "invoice_no"
    if "product" in t:
        return "product"
    if "pack" in t:
        return "pack_qty"
    if "list price" in t:
        return "list_price"
    if "grand total" in t:
        return "grand_total"
    if t.startswith("discount"):
        return "discount"
    if t == "tax 5%" or t.startswith("tax"):
        return "tax"
    if "retro" in t:
        return "retro"
    if "balance" in t:
        return "balance"
    if t in {"po date", "po  date"}:
        return "po_date"
    if "po no" in t:
        return "po_no"
    if t.replace(" ", "") == "podate":
        return "po_date"
    if "total invoice" in t:
        return "total"
    if t.strip() == "total":
        return "total"
    if "crel" in t:
        return "crel"
    if "dc" in t and "mdy" in t and ("%" in t or "other" in t or "hyper" in t):
        return "dc_charge"
    if "dc" in t and "mdy" in t:
        return "dc_mdy"
    if "hyper" in t and "%" not in t:
        return "hyper"
    if "g & g" in t or t == "g&g":
        return "g_and_g"
    if "sat" in t and "san" in t:
        return "sat_san"
    if "kha" in t:
        return "kha_wae_chan"
    if "remark" in t:
        return "remark"
    if "amount" == t:
        return "amount"
    if "po" in t and "date" in t:
        return "po_date"
    if t.startswith("po") and "date" not in t and "no" in t:
        return "po_no"
    return t.replace(" ", "_")


def _cell(ws: Worksheet, row: int, col: int | None) -> Any:
    if col is None:
        return None
    return ws.cell(row, col).value


def _parse_detail_sheet(
    ws: Worksheet,
    client_id: str,
    branch: str,
) -> tuple[list[Invoice], list[ImportErrorRow]]:
    """
    Parse a branch detail sheet.

    Excel groups multi-line invoices with header cells only on the first row.
    We load rows into a DataFrame, drop Excel "Total" footer rows, then
    pandas .ffill() Invoice No / dates / PO onto continuation lines before
    building Invoice + LineItem objects (subtotals are never stored).
    """
    headers = _header_map(ws)
    errors: list[ImportErrorRow] = []

    po_no_col = headers.get("po_no")
    po_date_col = headers.get("po_date")
    delivery_col = headers.get("delivery_date")
    inv_date_col = headers.get("invoice_date") or delivery_col
    due_col = headers.get("due_date")
    inv_no_col = headers.get("invoice_no")
    product_col = headers.get("product")
    qty_col = headers.get("pack_qty")
    qty_fa_col: int | None = None
    price_col = headers.get("list_price")
    total_col = headers.get("total")
    discount_col = headers.get("discount")
    tax_col = headers.get("tax")
    grand_col = headers.get("grand_total")

    if qty_col is not None:
        sub_left = str(ws.cell(2, qty_col).value or "").strip()
        sub_right = str(ws.cell(2, qty_col + 1).value or "").strip()
        if "ဖာ" in sub_left or sub_left.lower() == "fa" or (
            sub_right.lower() == "pcs"
            and sub_left.lower() not in {"pk", "pk (pcs)"}
        ):
            qty_fa_col = qty_col
            qty_col = qty_col + 1
        elif sub_right.lower() == "unit":
            qty_fa_col = qty_col + 1

    row1_vals = [
        ws.cell(1, c).value for c in range(1, min(6, (ws.max_column or 0) + 1))
    ]
    if (
        po_date_col == 1
        and inv_date_col == 3
        and row1_vals
        and str(row1_vals[0] or "").lower().startswith("po")
        and len(row1_vals) > 1
        and str(row1_vals[1] or "").lower().startswith("po")
    ):
        po_no_col = 1
        po_date_col = 2

    max_row = min(ws.max_row or 0, 5000)
    records: list[dict[str, Any]] = []
    empty_streak = 0
    for row_idx in range(3, max_row + 1):
        first_a = _cell(ws, row_idx, 1)
        product = _cell(ws, row_idx, product_col)
        inv_no_raw = _cell(ws, row_idx, inv_no_col)
        if product is None and inv_no_raw is None and first_a is None:
            empty_streak += 1
            if empty_streak >= 25:
                break
            continue
        empty_streak = 0

        po_no = ""
        po_date: Any = None
        if po_no_col and po_no_col != po_date_col:
            po_candidate = _cell(ws, row_idx, po_no_col)
            if po_candidate is not None:
                try:
                    po_date = parse_date(po_candidate)
                    po_no = ""
                except ValueError:
                    po_no = str(po_candidate).strip()
            if po_date_col:
                try:
                    parsed = parse_date(_cell(ws, row_idx, po_date_col))
                    if parsed is not None:
                        po_date = parsed
                except ValueError:
                    pass
        else:
            try:
                po_date = parse_date(_cell(ws, row_idx, po_date_col))
            except ValueError:
                raw_po = _cell(ws, row_idx, po_date_col)
                po_no = str(raw_po).strip() if raw_po not in (None, "") else ""

        try:
            invoice_date = parse_date(_cell(ws, row_idx, inv_date_col))
        except ValueError:
            invoice_date = None
        try:
            due_date = parse_date(_cell(ws, row_idx, due_col))
        except ValueError:
            due_date = None

        inv_no = ""
        if inv_no_raw not in (None, ""):
            inv_no = normalize_invoice_no(inv_no_raw)

        records.append(
            {
                "row": row_idx,
                "first_a": first_a,
                "invoice_no": inv_no or pd.NA,
                "po_no": po_no or pd.NA,
                "po_date": po_date if po_date is not None else pd.NA,
                "invoice_date": invoice_date if invoice_date is not None else pd.NA,
                "due_date": due_date if due_date is not None else pd.NA,
                "product": (
                    str(product).strip() if product not in (None, "") else ""
                ),
                "qty": _cell(ws, row_idx, qty_col),
                "qty_fa": _cell(ws, row_idx, qty_fa_col) if qty_fa_col else None,
                "price": _cell(ws, row_idx, price_col),
                "total": _cell(ws, row_idx, total_col),
                "discount": _cell(ws, row_idx, discount_col),
                "tax": _cell(ws, row_idx, tax_col),
                "grand": _cell(ws, row_idx, grand_col),
            }
        )

    if not records:
        return [], errors

    df = pd.DataFrame.from_records(records)

    # Drop Excel subtotal / Total footer rows — never store in line_items
    subtotal_mask = df.apply(
        lambda r: (
            is_total_row(r["first_a"])
            or is_total_row(r["product"])
            or is_total_row(r["invoice_no"] if pd.notna(r["invoice_no"]) else None)
            or is_total_row(r["po_no"] if pd.notna(r["po_no"]) else None)
        ),
        axis=1,
    )
    df = df.loc[~subtotal_mask].copy()
    if df.empty:
        return [], errors

    # Forward-fill parent invoice identity onto blank continuation rows
    for col in ("invoice_no", "po_no", "po_date", "invoice_date", "due_date"):
        df[col] = df[col].ffill()

    # Keep only real product lines
    df = df[df["product"].astype(str).str.strip() != ""].copy()
    df = df[df["invoice_no"].notna()].copy()
    df = df[~df["invoice_no"].map(is_total_row)].copy()

    invoices: list[Invoice] = []
    # Preserve sheet order while grouping
    df["_group"] = (
        df["invoice_no"].astype(str)
        + "|"
        + df["invoice_date"].map(
            lambda d: d.isoformat() if hasattr(d, "isoformat") else str(d)
        )
    )
    for _, group in df.groupby("_group", sort=False):
        first = group.iloc[0]
        inv_no = normalize_invoice_no(first["invoice_no"])
        if not inv_no or is_total_row(inv_no):
            continue
        po_no_val = first["po_no"]
        po_no = "" if pd.isna(po_no_val) else str(po_no_val).strip()
        inv = Invoice(
            client_id=client_id,
            invoice_no=inv_no,
            po_no=po_no,
            po_date=first["po_date"] if pd.notna(first["po_date"]) else None,
            invoice_date=(
                first["invoice_date"] if pd.notna(first["invoice_date"]) else None
            ),
            due_date=first["due_date"] if pd.notna(first["due_date"]) else None,
            branch=branch,
            product_label=str(first["product"]).strip(),
            lines=[],
            summary=InvoiceSummary(),
        )
        for _, row in group.iterrows():
            product_text = str(row["product"]).strip()
            if not product_text or is_total_row(product_text):
                continue
            total_raw = row["total"]
            grand_raw = row["grand"]
            price_raw = row["price"]
            foc = (
                is_foc_value(total_raw)
                or is_foc_value(grand_raw)
                or is_foc_value(price_raw)
            )
            inv.lines.append(
                LineItem(
                    product_description=product_text,
                    pack_qty=parse_number(row["qty"], allow_foc=True),
                    pack_qty_fa=parse_number(row["qty_fa"], allow_foc=True),
                    list_price=parse_number(price_raw, allow_foc=True),
                    line_total=(
                        parse_number(total_raw, allow_foc=True)
                        if total_raw not in (None, "")
                        else None
                    ),
                    discount_amount=parse_number(row["discount"], allow_foc=True),
                    tax_amount=parse_number(row["tax"], allow_foc=True),
                    grand_total=(
                        parse_number(grand_raw, allow_foc=True)
                        if grand_raw not in (None, "")
                        else None
                    ),
                    is_foc=foc,
                )
            )
        if inv.lines:
            invoices.append(inv)

    return invoices, errors


def _parse_summary_sheet(
    ws: Worksheet,
    client_id: str,
) -> tuple[list[dict[str, Any]], list[ImportErrorRow]]:
    headers = _header_map(ws)
    errors: list[ImportErrorRow] = []
    overlays: list[dict[str, Any]] = []

    inv_no_col = headers.get("invoice_no")
    inv_date_col = headers.get("invoice_date")
    po_date_col = headers.get("po_date")
    due_col = headers.get("due_date")
    product_col = headers.get("product")
    remark_col = headers.get("remark")
    tax_col = headers.get("tax")
    retro_col = headers.get("retro")

    branch_cols = {
        "CREL": headers.get("crel"),
        "DC MDY": headers.get("dc_mdy"),
        "Hyper": headers.get("hyper"),
        "G&G": headers.get("g_and_g"),
        "Sat San": headers.get("sat_san"),
        "Kha Wae Chan": headers.get("kha_wae_chan"),
        "One Stop Mart": headers.get("total") or headers.get("total_invoice"),
    }

    empty_streak = 0
    max_row = min(ws.max_row or 0, 5000)
    for row_idx in range(2, max_row + 1):
        inv_no = normalize_invoice_no(_cell(ws, row_idx, inv_no_col))
        if not inv_no:
            empty_streak += 1
            if empty_streak >= 25:
                break
            continue
        empty_streak = 0
        try:
            invoice_date = parse_date(_cell(ws, row_idx, inv_date_col))
            po_date = parse_date(_cell(ws, row_idx, po_date_col))
            due_date = parse_date(_cell(ws, row_idx, due_col))
        except ValueError as exc:
            errors.append(ImportErrorRow(ws.title, row_idx, str(exc)))
            continue

        product = _cell(ws, row_idx, product_col)
        remark = _cell(ws, row_idx, remark_col)
        branch_amounts: dict[str, float] = {}
        if client_id == "capital":
            for branch_name in ("CREL", "DC MDY", "Hyper", "G&G"):
                col = branch_cols.get(branch_name)
                if col is None:
                    continue
                raw = _cell(ws, row_idx, col)
                if raw in (None, ""):
                    continue
                amount = parse_number(raw)
                if amount:
                    branch_amounts[branch_name] = amount
        elif client_id == "makro":
            for branch_name in ("Sat San", "Kha Wae Chan"):
                col = branch_cols.get(branch_name)
                if col is None:
                    continue
                raw = _cell(ws, row_idx, col)
                if raw in (None, ""):
                    continue
                amount = parse_number(raw)
                if amount:
                    branch_amounts[branch_name] = amount

        overlay: dict[str, Any] = {
            "client_id": client_id,
            "invoice_no": inv_no,
            "invoice_date": invoice_date,
            "po_date": po_date,
            "due_date": due_date,
            "product_label": str(product).strip() if product else "",
            "remark": str(remark).strip() if remark else "",
            "branch_amounts": branch_amounts,
        }
        if tax_col:
            raw_tax = _cell(ws, row_idx, tax_col)
            if raw_tax not in (None, ""):
                overlay["tax_amount"] = parse_number(raw_tax)
        if retro_col:
            raw_retro = _cell(ws, row_idx, retro_col)
            if raw_retro not in (None, ""):
                overlay["retro_amount"] = parse_number(raw_retro)
        dc_col = headers.get("dc_charge")
        if dc_col:
            raw_dc = _cell(ws, row_idx, dc_col)
            if raw_dc not in (None, ""):
                # Excel stores DC as negative; persist positive magnitude
                overlay["delivery_charge"] = abs(parse_number(raw_dc))
        bal_col = headers.get("balance") or (
            headers.get("amount") if client_id == "makro" else None
        )
        if bal_col:
            raw_bal = _cell(ws, row_idx, bal_col)
            if raw_bal not in (None, ""):
                overlay["balance_to_receive"] = parse_number(raw_bal)
        if client_id == "one_stop_mart":
            tot_col = headers.get("total") or headers.get("total_invoice")
            if tot_col:
                raw_tot = _cell(ws, row_idx, tot_col)
                if raw_tot not in (None, ""):
                    overlay["total_invoice"] = parse_number(raw_tot)

        overlays.append(overlay)

    return overlays, errors


def _apply_summary_overlay(inv: Invoice, overlay: dict[str, Any]) -> None:
    if overlay.get("remark"):
        inv.summary.remark = overlay["remark"]
    if overlay.get("product_label") and not inv.product_label:
        inv.product_label = overlay["product_label"]
    if overlay.get("po_date") and not inv.po_date:
        inv.po_date = overlay["po_date"]
    if overlay.get("due_date") and not inv.due_date:
        inv.due_date = overlay["due_date"]
    if overlay.get("branch_amounts"):
        merged = dict(inv.summary.branch_amounts)
        merged.update(overlay["branch_amounts"])
        inv.summary.branch_amounts = merged
    if "tax_amount" in overlay:
        inv.summary.tax_amount = overlay["tax_amount"]
    if "retro_amount" in overlay:
        inv.summary.retro_amount = overlay["retro_amount"]
    if "delivery_charge" in overlay:
        inv.summary.delivery_charge = abs(float(overlay["delivery_charge"] or 0.0))
    if "balance_to_receive" in overlay:
        excel_balance = float(overlay["balance_to_receive"])
        # Capital Excel "Balance (To Receive)" is after DC. Store pre-DC
        # invoice balance so Payments can subtract DC again for Amount Due.
        if dc_applies_at_payment(inv.client_id):
            dc = abs(float(inv.summary.delivery_charge or 0.0))
            if "delivery_charge" in overlay:
                dc = abs(float(overlay["delivery_charge"] or 0.0))
                inv.summary.delivery_charge = dc
            inv.summary.balance_to_receive = round(excel_balance + dc, 2)
        else:
            inv.summary.balance_to_receive = excel_balance


def _summary_overlay_matches(inv: Invoice, overlay: dict[str, Any]) -> bool:
    """Match Summary row to a detail invoice by number, date, and branch amount."""
    if normalize_invoice_no(overlay.get("invoice_no")) != normalize_invoice_no(
        inv.invoice_no
    ):
        return False
    ov_date = overlay.get("invoice_date")
    if inv.invoice_date and ov_date and inv.invoice_date != ov_date:
        return False
    amounts = overlay.get("branch_amounts") or {}
    if inv.branch and amounts:
        # Prefer the branch whose Summary amount column is non-zero
        if inv.branch in amounts and float(amounts.get(inv.branch) or 0) != 0:
            return True
        # If only one branch amount present, accept
        nonzero = [b for b, v in amounts.items() if float(v or 0) != 0]
        if len(nonzero) == 1 and nonzero[0] == inv.branch:
            return True
        if inv.branch not in amounts:
            return False
        return float(amounts.get(inv.branch) or 0) != 0
    return True


def find_client_workbook(
    client_id: str, excel_dir: Path | str | None = None
) -> Path | None:
    client = get_client(client_id)
    directory = Path(excel_dir) if excel_dir else EXCEL_DATA_DIR
    matches = sorted(directory.glob(client.workbook_glob))
    matches = [p for p in matches if not p.name.startswith("~$")]
    return matches[0] if matches else None


def _fmt_summary_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        d = parse_date(value)
    except ValueError:
        return str(value).strip()
    return d.isoformat() if d else ""


def _num_or_zero(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return parse_number(value)
    except ValueError:
        return 0.0


def load_excel_summary_rows(
    client_id: str,
    *,
    year: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    month: int | None = None,
    branch: str | None = None,
    excel_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """
    Read the Excel **Summary - …** sheet as displayed in the workbook.

    This is the source of truth for the Summary UI (not detail-sheet imports).
    Filter with year_from/year_to (inclusive), or a single `year`.
    """
    # Normalize: single year → range of one year
    if year is not None and year_from is None and year_to is None:
        year_from = year
        year_to = year
    path = find_client_workbook(client_id, excel_dir)
    if path is None:
        return []

    wb = load_workbook(path, data_only=True)
    try:
        ws = None
        for name in wb.sheetnames:
            if name.lower().startswith("summary"):
                ws = wb[name]
                break
        if ws is None:
            return []

        headers = _header_map(ws)
        inv_no_col = headers.get("invoice_no")
        inv_date_col = headers.get("invoice_date")
        po_date_col = headers.get("po_date")
        due_col = headers.get("due_date")
        product_col = headers.get("product")
        remark_col = headers.get("remark")
        retro_col = headers.get("retro")
        tax_col = headers.get("tax")
        grand_col = headers.get("grand_total")
        amount_col = headers.get("amount")
        balance_col = headers.get("balance")
        total_col = headers.get("total")
        dc_charge_col = headers.get("dc_charge")

        # Capital: blank header column after Retro (= F+G+H+I+J)
        subtotal_col: int | None = None
        if client_id == "capital" and retro_col is not None:
            candidate = retro_col + 1
            # Prefer explicit blank / formula column before DC charge / Balance
            if dc_charge_col and candidate == dc_charge_col:
                pass
            else:
                subtotal_col = candidate

        branch_cols: dict[str, int | None] = {
            "CREL": headers.get("crel"),
            "DC MDY": headers.get("dc_mdy"),
            "Hyper": headers.get("hyper"),
            "G&G": headers.get("g_and_g"),
            "Sat San": headers.get("sat_san"),
            "Kha Wae Chan": headers.get("kha_wae_chan"),
        }

        rows: list[dict[str, Any]] = []
        empty_streak = 0
        max_row = min(ws.max_row or 0, 5000)
        for row_idx in range(2, max_row + 1):
            inv_no = normalize_invoice_no(_cell(ws, row_idx, inv_no_col))
            if not inv_no:
                empty_streak += 1
                if empty_streak >= 25:
                    break
                continue
            empty_streak = 0

            try:
                invoice_date = parse_date(_cell(ws, row_idx, inv_date_col))
            except ValueError:
                invoice_date = None
            if year_from is not None and (
                invoice_date is None or invoice_date.year < year_from
            ):
                continue
            if year_to is not None and (
                invoice_date is None or invoice_date.year > year_to
            ):
                continue
            if month is not None and (
                invoice_date is None or invoice_date.month != month
            ):
                continue

            crel = _num_or_zero(_cell(ws, row_idx, branch_cols["CREL"]))
            dc_mdy = _num_or_zero(_cell(ws, row_idx, branch_cols["DC MDY"]))
            hyper = _num_or_zero(_cell(ws, row_idx, branch_cols["Hyper"]))
            gng = _num_or_zero(_cell(ws, row_idx, branch_cols["G&G"]))
            sat = _num_or_zero(_cell(ws, row_idx, branch_cols["Sat San"]))
            kha = _num_or_zero(_cell(ws, row_idx, branch_cols["Kha Wae Chan"]))

            if branch:
                if client_id == "capital":
                    amap = {
                        "CREL": crel,
                        "DC MDY": dc_mdy,
                        "Hyper": hyper,
                        "G&G": gng,
                    }
                    if float(amap.get(branch, 0) or 0) == 0:
                        continue
                elif client_id == "makro":
                    amap = {"Sat San": sat, "Kha Wae Chan": kha}
                    if float(amap.get(branch, 0) or 0) == 0:
                        continue
                elif client_id == "one_stop_mart" and branch != "One Stop Mart":
                    continue

            product = _cell(ws, row_idx, product_col)
            product_text = str(product).strip() if product not in (None, "") else ""
            remark = _cell(ws, row_idx, remark_col)
            remark_text = str(remark).strip() if remark not in (None, "") else ""

            if client_id == "capital":
                retro = _num_or_zero(_cell(ws, row_idx, retro_col))
                subtotal = _num_or_zero(_cell(ws, row_idx, subtotal_col))
                if not subtotal:
                    subtotal = round(crel + dc_mdy + hyper + gng + retro, 2)
                dc_charge = _num_or_zero(_cell(ws, row_idx, dc_charge_col))
                balance = _num_or_zero(_cell(ws, row_idx, balance_col))
                rows.append(
                    {
                        "Po Date": _fmt_summary_date(
                            _cell(ws, row_idx, po_date_col)
                        ),
                        "Invoice Date": _fmt_summary_date(
                            _cell(ws, row_idx, inv_date_col)
                        ),
                        "Due Date": _fmt_summary_date(_cell(ws, row_idx, due_col)),
                        "Invoice No": inv_no,
                        "Product": product_text,
                        "CREL": crel,
                        "DC MDY": dc_mdy,
                        "Hyper": hyper,
                        "G & G": gng,
                        "Retro (3%)": retro,
                        "Subtotal": subtotal,
                        "DC Mdy6%, Other 4%, Hyper 0%": dc_charge,
                        "Balance (To Receive)": balance,
                        "Remark": remark_text,
                    }
                )
            elif client_id == "makro":
                tax = _num_or_zero(_cell(ws, row_idx, tax_col))
                grand = _num_or_zero(_cell(ws, row_idx, grand_col))
                retro = _num_or_zero(_cell(ws, row_idx, retro_col))
                amount = _num_or_zero(_cell(ws, row_idx, amount_col))
                rows.append(
                    {
                        "PO Date": _fmt_summary_date(
                            _cell(ws, row_idx, po_date_col)
                        ),
                        "Invoice Date": _fmt_summary_date(
                            _cell(ws, row_idx, inv_date_col)
                        ),
                        "Due Date": _fmt_summary_date(_cell(ws, row_idx, due_col)),
                        "Invoice No": inv_no,
                        "Product Description": product_text,
                        "Sat San": sat,
                        "Kha Wae Chan": kha,
                        "Tax 5%": tax,
                        "Grand Total": grand,
                        "Retro ( -5% )": retro,
                        "Amount": amount,
                        "Remark": remark_text,
                    }
                )
            else:
                total_inv = _num_or_zero(_cell(ws, row_idx, total_col))
                retro = _num_or_zero(_cell(ws, row_idx, retro_col))
                balance = _num_or_zero(_cell(ws, row_idx, balance_col))
                rows.append(
                    {
                        "PO Date": _fmt_summary_date(
                            _cell(ws, row_idx, po_date_col)
                        ),
                        "Invoice Date": _fmt_summary_date(
                            _cell(ws, row_idx, inv_date_col)
                        ),
                        "Due Date": _fmt_summary_date(_cell(ws, row_idx, due_col)),
                        "Invoice No": inv_no,
                        "Product": product_text,
                        "Total Invoice": total_inv,
                        "Retro (5%,10%)": retro,
                        "Balance (To Receive)": balance,
                    }
                )
        return rows
    finally:
        wb.close()
