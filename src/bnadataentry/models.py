"""Dataclasses for invoices, line items, and summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class LineItem:
    product_description: str
    pack_qty: float = 0.0  # Capital Pk / Makro Pcs — used for Total calc
    pack_qty_fa: float = 0.0  # Makro ဖာ; Capital Pack Qty Unit (usually 0)
    list_price: float = 0.0
    line_total: float | None = None
    discount_amount: float = 0.0
    tax_amount: float = 0.0
    grand_total: float | None = None
    is_foc: bool = False
    id: int | None = None
    invoice_id: int | None = None


@dataclass
class InvoiceSummary:
    branch_amounts: dict[str, float] = field(default_factory=dict)
    tax_amount: float | None = None
    retro_amount: float | None = None
    delivery_charge: float = 0.0  # positive deduction (Capital DC, etc.)
    balance_to_receive: float | None = None
    remark: str = ""
    tax_pct_override: float | None = None
    retro_pct_override: float | None = None
    invoice_id: int | None = None


@dataclass
class Payment:
    invoice_id: int
    payment_date: date
    amount: float
    method: str = ""
    reference: str = ""
    remark: str = ""
    id: int | None = None
    created_at: str | None = None


@dataclass
class Invoice:
    client_id: str
    invoice_no: str
    invoice_date: date | None = None
    po_no: str = ""
    po_date: date | None = None
    due_date: date | None = None
    branch: str = ""
    product_label: str = ""
    lines: list[LineItem] = field(default_factory=list)
    summary: InvoiceSummary = field(default_factory=InvoiceSummary)
    id: int | None = None

    def to_filter_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "invoice_no": self.invoice_no,
            "invoice_date": self.invoice_date,
            "branch": self.branch,
        }
