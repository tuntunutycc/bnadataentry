"""Excel export for the reconciliation screen. Import is not used."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from bnadataentry.expected_payment import MAKRO, money
from bnadataentry.reconciliation import ReconciliationRepository, SupermarketInvoice

_RECON_HEADERS = (
    "Invoice No.",
    "Supermarket",
    "Shop",
    "Invoice date",
    "Due date",
    "Credit days",
    "Discount %",
    "Remark",
    "Total",
    "Expected",
    "Received",
    "Variance",
    "Status",
    "Tax %",
    "Retro %",
    "Received date",
    "Retro cut-off",
)

_LINE_HEADERS = (
    "Invoice No.",
    "Category",
    "Sub-category",
    "Product",
    "Unit",
    "Entered quantity",
    "Pieces per pack",
    "Price per piece",
    "Quantity (pieces)",
    "Amount",
    "Tax exempt",
)

_PAYMENT_HEADERS = (
    "Invoice No.",
    "Payment date",
    "Received",
    "Retro %",
    "Delivery %",
    "Tax %",
    "Retro cut-off",
)

_CATEGORY_HEADERS = ("Category", "Sub-category", "Total amount")
_ITEM_HEADERS = (
    "Product",
    "Category",
    "Sub-category",
    "Quantity (pieces)",
    "Amount",
)


def export_filename(supermarket: str, shop: str) -> str:
    """A download name made only of letters, numbers, and hyphens."""
    raw = f"{supermarket}-{shop}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe.strip('-')}.xlsx"


def reconciliation_workbook_bytes(
    repo: ReconciliationRepository, supermarket: str, shop: str
) -> bytes:
    """Workbook for one shop: reconciliation, lines, payments, and both summaries."""
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    invoices = repo.list_invoices(supermarket, shop)
    by_id = {invoice.id: invoice for invoice in invoices}
    _write_sheet(
        wb,
        "Reconciliation",
        _RECON_HEADERS,
        _reconciliation_rows(repo, by_id, supermarket, shop),
    )
    _write_sheet(wb, "Lines", _LINE_HEADERS, _line_rows(invoices))
    _write_sheet(wb, "Payments", _PAYMENT_HEADERS, _payment_rows(repo, invoices))
    amounts, _missing = repo.city_category_summary(supermarket, shop)
    _write_sheet(
        wb,
        "By category",
        _CATEGORY_HEADERS,
        [
            {
                "Category": row["Category"],
                "Sub-category": row["Sub-category"],
                "Total amount": row["Total amount"],
            }
            for row in amounts
        ],
    )
    _write_sheet(wb, "By product", _ITEM_HEADERS, list(repo.city_item_summary(supermarket, shop)))
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _reconciliation_rows(
    repo: ReconciliationRepository,
    by_id: dict[int | None, SupermarketInvoice],
    supermarket: str,
    shop: str,
) -> list[dict[str, object]]:
    exported: list[dict[str, object]] = []
    for row in repo.report_rows(supermarket, shop):
        invoice = by_id.get(row["id"])
        exported.append(
            {
                "Invoice No.": row["Invoice No."],
                "Supermarket": row["Supermarket"],
                "Shop": row["Shop"],
                "Invoice date": row["Invoice Date"],
                "Due date": row["Due Date"],
                "Credit days": invoice.credit_days if invoice is not None else "",
                "Discount %": (
                    round(invoice.discount_percentage * 100, 2)
                    if invoice is not None
                    else ""
                ),
                "Remark": invoice.remark if invoice is not None else "",
                "Total": row["Total Invoice Amount"],
                "Expected": row["Expected Final Payment"],
                "Received": row["Actual Received Amount"],
                "Variance": row["Variance"],
                "Status": row["Status"],
                "Tax %": row.get("Tax %", "") if supermarket == MAKRO else "",
                "Retro %": row.get("Retro %", "") if supermarket == MAKRO else "",
                "Received date": (
                    row.get("Received Date", "") if supermarket == MAKRO else ""
                ),
                "Retro cut-off": (
                    row.get("Retro cut-off", "") if supermarket == MAKRO else ""
                ),
            }
        )
    return exported


def _line_rows(invoices) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for invoice in invoices:
        for line in invoice.categories:
            rows.append(
                {
                    "Invoice No.": invoice.invoice_number,
                    "Category": line.category,
                    "Sub-category": line.subcategory,
                    "Product": line.product_name,
                    "Unit": line.unit,
                    "Entered quantity": line.entered_quantity,
                    "Pieces per pack": line.pieces_per_pack,
                    "Price per piece": line.price_per_piece,
                    "Quantity (pieces)": line.base_quantity,
                    "Amount": money(line.amount),
                    "Tax exempt": "Yes" if line.tax_exempt else "No",
                }
            )
    return rows


def _payment_rows(repo: ReconciliationRepository, invoices) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for invoice in invoices:
        assert invoice.id is not None
        for payment in repo.list_payments(invoice.id):
            rows.append(
                {
                    "Invoice No.": invoice.invoice_number,
                    "Payment date": payment.payment_date.isoformat(),
                    "Received": money(payment.actual_received_amount),
                    "Retro %": round(payment.retro_percentage * 100, 2),
                    "Delivery %": round(payment.delivery_charge_percentage * 100, 2),
                    "Tax %": round(payment.tax_percentage * 100, 2),
                    "Retro cut-off": (
                        payment.retro_cut_off_date.isoformat()
                        if payment.retro_cut_off_date
                        else ""
                    ),
                }
            )
    return rows


def _write_sheet(
    wb: Workbook,
    name: str,
    headers: tuple[str, ...],
    rows: list[dict[str, object]],
) -> Worksheet:
    ws = wb.create_sheet(name[:31])
    ws.append(list(headers))
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row.get(header, "") for header in headers])
    return ws
