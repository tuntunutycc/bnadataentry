# Compliance report — requirements__3_.pdf

**Date:** 2026-10-24  
**Source of truth:** [`requirements_3.md`](../requirements_3.md) (extracted from `requirements__3_.pdf`)  
**Live surface audited:** Streamlit reconciliation (`app.py` → `render_reconciliation`)  
**Baseline tests:** `142 passed, 0 skipped` (`.venv/bin/python -m pytest -q`)

Status meanings:

- **Pass** — implemented and covered by code and/or automated tests as stated in the PDF.
- **Partial** — mostly present; gap noted honestly.
- **Fail** — required behaviour missing or wrong.

---

## Summary

| Status | Count |
|--------|------:|
| Pass | 28 |
| Partial | 3 |
| Fail | 0 |
| **Total IDs** | **31** |

Known Partials (no code change in this audit):

1. **OSM tax-exempt** — line flag is stored; OSM payment formula does not branch on it (PDF only spells exempt paths for Capital and Makro).
2. **NFR-02** — line editor uses Streamlit widgets with a sensible field order; full mouse-free Tab/Enter flow is not proven by tests.
3. **MAK-RETRO-STAGE file import** — Received Date / Retro Cut Off exist on the live report and Excel export; importing Makro “sales summary & payment” workbooks was removed by design.

---

## A. Payment formulas

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| CAP-TAX | Pass | `CapitalStrategy` in `expected_payment.py`; no tax add step | Caption on new Capital invoice: prices include 5% tax. No tax % input on Capital. |
| CAP-BALANCE | Pass | `CapitalStrategy.calculate`; `test_capital_taxable_delivery_uses_the_ex_tax_base` | Balance = net after discount × (1 − retro). |
| CAP-DC-TAXABLE | Pass | `/105 × 100 × DC` in `CapitalStrategy`; same test as above (100000, 3%, 4% → 93304.76) | |
| CAP-DC-EXEMPT | Pass | Exempt branch without /105; `test_capital_tax_exempt_invoice_uses_balance_times_delivery`, `test_capital_mixed_invoice_splits_the_delivery_formula` | Mixed invoices split taxable vs exempt DC. |
| CAP-DC-STAGE | Pass | `default_terms` Capital DC=0 before payment; rates from `create_payment` / `_rate_inputs` | Shop defaults Hyper 0%, DC MDY 6%, CREL/G&G 4% via `capital_delivery_rate`. |
| MAK-TAX-RETRO | Pass | `MakroStrategy`; `test_makro_adds_five_percent_tax_then_deducts_five_percent_retro` | Tax then retro on grand total. |
| MAK-EXEMPT | Pass | Exempt share skips tax; `test_makro_tax_exempt_items_do_not_receive_the_five_percent_tax`, `test_makro_fully_exempt_invoice_keeps_the_total_as_the_grand_total` | |
| MAK-NO-TAX-NO-RETRO | Pass | Tax 0 and retro 0 → expected = after-discount net; covered in reconciliation / requirements tests | |
| MAK-RETRO-STAGE | Partial | Report columns + payment cut-off in `recon_ui.py` `_rate_inputs` / `_display_records`; export `test_makro_export_keeps_tax_retro_and_received_date` | Dates on UI and Excel export. Office Makro sales-file **import** removed. |
| OSM-NO-TAX | Pass | `OneStopMartStrategy` has no tax add; `test_one_stop_mart_combines_dynamic_rates_and_adds_no_tax` | |
| OSM-COMBINED | Pass | Back margin + delivery incentive; `test_one_stop_mart_rates_are_not_locked_at_ten_percent`, `test_one_stop_mart_payment_uses_the_rates_entered_on_the_payment` | Combined rate not fixed at 10%. |

---

## B. Client feedback & new requirements

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| CF-01 | Pass | No “Tax included” checkbox in `recon_ui.py` (grep empty); Capital forced tax-included in `_normalize_tax_treatment` | PDF says the checkbox **may be removed**. Older `phase_2_brd.md` REQ-01 said add it — **PDF wins**. |
| CF-02 | Pass | Capital payment `number_input("Retro %")` key `recon_capital_retro_*`; `test_capital_retro_and_delivery_follow_the_entered_rates` | Applies to Hyper and other Capital shops. |
| NR-01 | Pass | Category summary tab: `city_category_summary` + `city_item_summary` in `_render_city_summary` | Amount by category+sub-category; pieces by product (see FR-07). |
| NR-02 | Pass | “Add line” in `_city_line_editor`; screen tests create multi-line flows | |

---

## C. Functional requirements

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| FR-01 | Pass | `selectbox("Unit", UNITS)` with Piece/Pack; `test_two_packs_of_twelve_are_stored_as_twenty_four_pieces` | |
| FR-02 | Pass | Cascading category → sub-category → product selectboxes from `list_products()` | Invoice entry does not free-type product names. Product **admin** still types names when creating master rows. |
| FR-03 | Pass | `products.pieces_per_pack`; product form “Pieces per pack” | |
| FR-04 | Pass | `_city_mart_line` packs → `base_quantity = entered × pieces_per_pack` | |
| FR-05 | Pass | Line amount = entered × unit price; invoice total = sum of lines | Pack unit price = price_per_piece × pieces_per_pack. |
| FR-06 | Pass | `_schedule_inputs`; `test_due_date_follows_credit_days_and_a_manual_date_is_kept` | Auto due + manual override. |
| FR-07 | Pass | `_render_city_summary`; `test_amount_groups_by_category_and_quantity_stays_on_each_item` | Category view: amount only. Item view: piece qty per SKU. Matches PDF FR-07 (not older “qty+amount by category only”). |

---

## D. Non-functional

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| NFR-01 | Pass | Snapshot columns on `invoice_categories`; `test_saved_invoice_keeps_its_price_after_the_product_changes`, `test_used_product_is_hidden_and_the_invoice_keeps_its_copy` | Master price/pack change does not rewrite saved lines. |
| NFR-02 | Partial | Streamlit field order category → sub → product → qty → unit → Add line | No automated keyboard-only UAT. Mouse still needed for some widgets. |

---

## E. UAT scenarios

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| UAT-1 | Pass | `test_two_packs_of_twelve_are_stored_as_twenty_four_pieces` | 2 packs × 12 → 24 pieces + amount. |
| UAT-2 | Pass | `test_due_date_follows_credit_days_and_a_manual_date_is_kept` | Credit days drive due date; override kept. |
| UAT-3 | Pass | `test_amount_groups_by_category_and_quantity_stays_on_each_item` | Snacks amount rolled; Potato chips / Chocolate / Milk qty separate. |

---

## F. Capital enhancement

| ID | Status | Evidence | Notes |
|----|--------|----------|-------|
| CAP-DISC | Pass | `_discount_inputs` 0/3/4/5%; `discount_note` remark; `test_discount_is_deducted_and_written_in_the_remark` (all three shops) | Discount taken before retro/tax/OSM margins. |
| CAP-EXEMPT-FORMULA | Pass | Line tax-exempt + Capital DC split; mixed/exempt tests above | Same mechanism used for Makro tax skip. OSM does not change payment on exempt (see Partial note). |
| CAP-EDIT-DELETE | Pass | `_render_edit_controls` / edit form / `delete_invoice`; `test_reconciliation_screen_saves_and_deletes`, `test_capital_discount_remark_exempt_row_and_delete` | |

---

## Excel export (supporting PDF Makro date columns & reports)

Not a separate PDF ID, but verified for the live shop:

- `recon_export.reconciliation_workbook_bytes` sheets: Reconciliation, Lines, Payments, By category, By product.
- Makro Received date / Retro cut-off on Reconciliation sheet (`tests/test_recon_export.py`).
- UI: **Export Excel** on Reconciliation report and Category summary.

---

## Intentional non-matches vs older docs

| Older doc | PDF / live | Audit ruling |
|-----------|------------|--------------|
| `phase_2_brd.md` REQ-01 add Tax-included checkbox | CF-01 remove checkbox | Pass without checkbox |
| `phase_2_brd.md` REQ-03 qty+amount by category | FR-07 amount by category; qty by item | Pass as implemented |
| PDF title “City Mart …” | Live Capital / Makro / One Stop Mart | City Mart is not a supermarket in the app |

---

## Fixes applied while running the audit baseline

These were blockers for a clean pytest run; not requirement gaps:

1. Restored missing `_ensure_product_is_active` in `src/bnadataentry/db.py` (soft-delete migration).
2. Repaired corrupted `client_workbook_bytes` / `export_client_workbook` in `src/bnadataentry/excel_export.py`.

No payment formula or UI requirement behaviour was changed for compliance scoring.
