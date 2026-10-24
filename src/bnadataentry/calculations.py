"""Line-item and invoice summary calculations."""

from __future__ import annotations

from bnadataentry.clients import ClientConfig, get_client
from bnadataentry.models import Invoice, InvoiceSummary, LineItem


def compute_line_totals(line: LineItem) -> LineItem:
    """Fill line_total and grand_total; FOC lines stay at zero."""
    if line.is_foc:
        line.line_total = 0.0
        line.discount_amount = 0.0
        line.tax_amount = 0.0
        line.grand_total = 0.0
        return line

    if line.line_total is None:
        line.line_total = round(line.pack_qty * line.list_price, 6)

    base = line.line_total - line.discount_amount
    if line.grand_total is None:
        line.grand_total = round(base + line.tax_amount, 6)
    return line


def effective_tax_pct(client: ClientConfig, summary: InvoiceSummary) -> float:
    if summary.tax_pct_override is not None:
        return summary.tax_pct_override
    return client.default_tax_pct


def effective_retro_pct(client: ClientConfig, summary: InvoiceSummary) -> float:
    if summary.retro_pct_override is not None:
        return summary.retro_pct_override
    return client.default_retro_pct


def sum_line_amounts(lines: list[LineItem]) -> float:
    total = 0.0
    for line in lines:
        compute_line_totals(line)
        total += line.line_total or 0.0
    return round(total, 6)


def dc_applies_at_payment(client_id: str) -> bool:
    """True when Delivery Charge is applied at payment, not on the invoice balance."""
    return client_id == "capital"


def capital_dc_charge(subtotal: float, branch: str) -> float:
    """
    Capital DC amount (positive deduction).

    Excel formula stores a negative value: =-((K/105)*100)*rate%
    with Hyper 0%, DC MDY 6%, other branches 4%.
    Applied at **payment** stage for Capital (not in invoice balance).
    """
    if branch == "Hyper":
        rate = 0.0
    elif branch == "DC MDY":
        rate = 0.06
    else:
        rate = 0.04
    if not subtotal or not rate:
        return 0.0
    # Excel subtotal K can be negative after retro; use abs for rate base
    base = abs(float(subtotal))
    return round(((base / 105.0) * 100.0) * rate, 2)


def suggest_capital_dc(invoice_balance: float, branch: str) -> float:
    """Suggested Capital DC from invoice balance (after retro) + branch rate."""
    return capital_dc_charge(invoice_balance, branch)


def balance_to_receive_amount(
    merchandise: float,
    tax_amount: float,
    retro_amount: float,
    delivery_charge: float = 0.0,
) -> float:
    """
    Invoice balance formula.

    ``retro_amount`` is stored signed (typically negative).
    ``delivery_charge`` is a non-negative deduction.

    For Capital, pass ``delivery_charge=0`` — DC is applied later at payment.
    Equivalent to::
        merchandise + tax - abs(retro) - delivery_charge
    when retro ≤ 0.
    """
    return round(
        float(merchandise or 0.0)
        + float(tax_amount or 0.0)
        + float(retro_amount or 0.0)
        - abs(float(delivery_charge or 0.0)),
        6,
    )


def compute_summary(
    client_id: str,
    lines: list[LineItem],
    summary: InvoiceSummary | None = None,
    *,
    branch: str = "",
    use_stored_amounts: bool = False,
) -> InvoiceSummary:
    """
    Compute tax, retro, delivery charge, and invoice balance.

    - Tax and retro use override % when set, else client defaults.
    - Retro is stored as a negative amount (money given back).
    - Delivery charge is a non-negative deduction.
    - Capital: invoice balance = merch + tax + retro (DC deferred to payment).
    - Other clients: balance = merch + tax + retro − delivery_charge.
    - If absolute amounts are already set and use_stored_amounts is True,
      keep those values (manual edits).
    """
    client = get_client(client_id)
    result = summary or InvoiceSummary()
    for line in lines:
        compute_line_totals(line)

    merchandise = sum_line_amounts(lines)

    if branch and branch not in result.branch_amounts and merchandise:
        result.branch_amounts = {**result.branch_amounts, branch: merchandise}

    tax_pct = effective_tax_pct(client, result)
    retro_pct = effective_retro_pct(client, result)

    if not use_stored_amounts or result.tax_amount is None:
        result.tax_amount = round(merchandise * tax_pct / 100.0, 6)

    taxable_base = merchandise + (result.tax_amount or 0.0)
    if not use_stored_amounts or result.retro_amount is None:
        # Retro reduces receivable; store as negative
        result.retro_amount = round(-taxable_base * retro_pct / 100.0, 6)

    # Normalize DC to non-negative magnitude (Capital: set on Payments, not here)
    result.delivery_charge = abs(float(result.delivery_charge or 0.0))
    if dc_applies_at_payment(client_id) and not use_stored_amounts:
        # Do not auto-fill DC on invoice create/edit; leave stored value or 0.
        pass

    if not use_stored_amounts or result.balance_to_receive is None:
        dc_in_balance = (
            0.0
            if dc_applies_at_payment(client_id)
            else result.delivery_charge
        )
        result.balance_to_receive = balance_to_receive_amount(
            merchandise,
            result.tax_amount or 0.0,
            result.retro_amount or 0.0,
            dc_in_balance,
        )
    return result


def refresh_invoice(invoice: Invoice, *, use_stored_amounts: bool = False) -> Invoice:
    invoice.summary = compute_summary(
        invoice.client_id,
        invoice.lines,
        invoice.summary,
        branch=invoice.branch,
        use_stored_amounts=use_stored_amounts,
    )
    return invoice
