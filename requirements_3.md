# Requirements checklist (requirements__3_.pdf)

Source: uploaded `requirements__3_.pdf` (8 pages).  
This file is the audit checklist. When this PDF and older docs disagree, **this PDF wins**.

Live app scope: Capital, Makro, and One Stop Mart only (not City Mart as a supermarket). Product/UI FRs below apply to those three.

---

## A. Payment formulas

### CAP-TAX — Capital: no external tax add

Capital item prices already include 5% tax. Do not add tax again on the invoice total.

### CAP-BALANCE — Capital invoice balance after retro

`Invoice Balance = Total Invoice Amount × (1 − Retro %)`  
(Equivalently: total minus total × Retro %.)

### CAP-DC-TAXABLE — Capital delivery on tax-included share

DC (0%, 4%, or 6%) is deducted at payment. For tax-included goods:

`Expected = Invoice Balance − (((Invoice Balance / 105) × 100) × DC %)`

### CAP-DC-EXEMPT — Capital delivery on tax-exempt share

For tax-exempt goods:

`Expected = Invoice Balance − (Invoice Balance × DC %)`  
(No /105 step.)

### CAP-DC-STAGE — Capital DC only at payment

Delivery charges (%) are applied at the final payment step, not as a fixed invoice-only figure before payment.

### MAK-TAX-RETRO — Makro tax then retro (taxable goods)

`Grand Total = Total Invoice + (Total Invoice × Tax %)`  
`Expected = Grand Total − (Grand Total × Retro %)`  
Usual rates: Tax +5%, Retro −5%.

### MAK-EXEMPT — Makro tax-exempt goods

`Grand Total = Total Invoice` (no tax add).  
`Expected = Grand Total − (Grand Total × Retro %)` when retro applies.

### MAK-NO-TAX-NO-RETRO — Makro neither tax nor retro

`Grand Total = Total Invoice`  
`Expected = Grand Total`

### MAK-RETRO-STAGE — Makro retro at payment / cut-off

Retro (−5%) is applied at payment. Retro may cover only part of goods (due not yet reached). Need **Received Date** and **Retro Cut Off Date** on the Makro sales/payment view (or export).

### OSM-NO-TAX — One Stop Mart: no external tax add

Prices already include tax. Do not add +5% tax on the invoice.

### OSM-COMBINED — One Stop Mart back margin + delivery incentive

Deduct combined `(Back Margin % + Delivery Incentive %)` from Total Invoice Amount.  
Back Margin is the same idea as Retro. Combined rate is **not** locked at 10%; both rates can change.

---

## B. Client feedback (08.10.2026)

### CF-01 — Remove Capital “Tax included” checkbox

Capital always has tax in the item price, so the invoice checkbox `["Tax included in the invoice amount"]` may be removed.

### CF-02 — Adjustable Capital (Hyper) retro

Capital Hyper Retro is not always 3%. User must be able to choose or enter Retro %, similar to Delivery Charges.

### NR-01 — Category summary feature

Show quantity and total amount by category (see also FR-07 for how quantity vs amount are split).

### NR-02 — Multi-item lines on one invoice

An invoice can have more than one product. Support adding another item/line while entering the invoice.

---

## C. Functional requirements (UI & business logic)

### FR-01 — Unit Piece or Pack

Beside quantity, a dropdown for Unit: **Piece** or **Pack**.

### FR-02 — Cascading Category / Sub-category / Product

No free typing for category/sub-category/product. Choose only from master data, step by step.

### FR-03 — Pieces per pack on product master

Each product stores pieces per pack (conversion rate).

### FR-04 — Pack converts to base pieces

When Unit is Pack: store `Input Quantity × Pieces per Pack` as base unit (pieces).

### FR-05 — Auto line amount

`Total Amount = Quantity × Unit Price` (pack unit price = price per piece × pieces per pack). Auto-calculate and show.

### FR-06 — Credit days and auto due date

Set Credit Days on the invoice. Due Date = Invoice Date + Credit Days automatically. User may override Due Date manually.

### FR-07 — Category vs item reporting

- **Category level:** group by Category and Sub-category; show **Total Amount** (money) as the main figure.
- **Item level:** show **Quantity in pieces** per Product (SKU), not summed across different products into one qty.

---

## D. Non-functional

### NFR-01 — Snapshot integrity

Changing product master price or pack size later must not rewrite historical invoice line snapshots.

### NFR-02 — Keyboard-friendly entry

Prefer Tab / Enter data entry without requiring the mouse for every field (usability goal).

---

## E. UAT acceptance scenarios

### UAT-1 — Unit conversion

Sell Product A as **2 Pack**; master says 12 pieces/pack. Report shows **24 pieces** and the correct amount.

### UAT-2 — Due date automation

Invoice Date = today, Credit Days = 7 → Due Date moves +7 days. User can still override Due Date.

### UAT-3 — Category summary logic

For Category “Snacks”, report shows one rolled-up **amount**. Potato chips and chocolate quantities stay on **separate item rows** (not merged into one quantity of 7).

---

## F. Capital system enhancement

### CAP-DISC — Discount 3% / 4% / 5%

Choose discount percent off Total Invoice Amount before Capital retro/DC (and similarly usable on other shops). Write the discount money into the **remark** (editable).

### CAP-EXEMPT-FORMULA — Tax-exempt items exclude tax DC path

Tax formula (/105 delivery) must not apply to tax-exempt lines (e.g. dairy). Use exempt DC path instead.

### CAP-EDIT-DELETE — Edit and delete saved invoices

Recall a saved invoice to modify it, or delete/void it entirely.
