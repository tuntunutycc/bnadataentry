# System and Database Design

This document describes the reconciliation system as it is implemented today. The running application is a Streamlit screen over one SQLite database. Payment formulas are Python strategy classes. There is no separate API server and no supermarket-profile table.

Primary code:

| Area | Module |
|---|---|
| Screen and navigation | `app.py`, `src/bnadataentry/recon_ui.py` |
| Invoice, product, and payment persistence | `src/bnadataentry/reconciliation.py` |
| Schema and migrations | `src/bnadataentry/db.py` |
| Payment formulas | `src/bnadataentry/expected_payment.py` |
| Database file | `data/app.db` |

`app.py` `main()` opens that database and calls `render_reconciliation`. Older office invoice screens remain in the repository and share the same SQLite file. They are not the screen the service serves.

## 1. System architecture

### 1.1 Shape of the system

```text
Browser
  │
  ▼
Streamlit (app.py → recon_ui.render_reconciliation)
  │
  ├── Product admin (signed in)
  │     └── ReconciliationRepository.save_product / delete_product
  │
  ├── Invoice entry (no sign-in)
  │     └── copies the current product price and pack size onto the line
  │
  └── Payment and reports
        └── ReconciliationRepository.terms_for
              └── expected_payment.strategy_for(supermarket).calculate
                    ├── CapitalStrategy
                    ├── MakroStrategy
                    └── OneStopMartStrategy
  │
  ▼
SQLite data/app.db
```

Percentages are stored as fractions. `0.04` means 4%. Money results pass through `money()`, which rounds to cents with `round(value + 1e-9, 2)`.

### 1.2 From master data to a saved invoice

1. An administrator opens **Products** in the left pane. The list is shared by Capital, Makro, and One Stop Mart.
2. Adding or deleting a product requires the username and password in the environment variables `BNA_PRODUCT_USER` and `BNA_PRODUCT_PASSWORD`. Those values are not stored in the database. A successful sign-in sets the session flag `product_admin_ok`.
3. `save_product` writes three tables: `product_categories`, `product_subcategories`, and `products`. A category and sub-category row are created when the names are new. The product row stores `pieces_per_pack` and `price_per_piece`.
4. A clerk picks a supermarket, then a shop, in the left pane. That choice is not a login. Shop names and delivery defaults come from the `SHOPS` map and `capital_delivery_rate` in code.
5. On **New invoice**, each line chooses Category, then Sub-category, then Product from `list_products()`. The clerk enters a quantity and Piece or Pack, and may mark the line tax exempt.
6. The form copies the product's current `pieces_per_pack` and `price_per_piece` onto the line. The invoice does not store `products.id`.
7. `create_invoice` normalizes the lines, sets the invoice total to the sum of line amounts, writes the 3%, 4%, or 5% discount into `remark` when the remark is empty, and inserts `supermarket_invoices` plus one `invoice_categories` row per line.
8. **Record payment** stores the money received and the rates that belong to that supermarket. Capital retro and delivery are taken from the payment. Makro tax and retro are written back onto the invoice. One Stop Mart back margin and delivery incentive stay on the payment.
9. The report calls `terms_for`, then `expected_payment`. The matching strategy returns the expected transfer and the steps shown on the report. Status becomes Pending, Partial, or Paid from received versus expected.

Changing a product later updates only `products`. Saved `invoice_categories` rows are left as they were. That is the snapshot rule described in section 2.4.

### 1.3 What is not a database profile

Capital, Makro, and One Stop Mart are the tuple `SUPERMARKETS`. Their shops are the `SHOPS` dictionary:

| Supermarket | Shops | Delivery default used on a Capital payment |
|---|---|---|
| Capital | CREL Distribution Center, DC MDY, G&G, Hyper | CREL and G&G 4%, DC MDY 6%, Hyper 0% |
| Makro | Makro-Sat Sun, Makro - Kha Wei Chan | none |
| One Stop Mart | One Stop Mart | none |

Tax treatment, the divide-by-105 rule, and which rates are editable are also code, not rows. An unknown supermarket name raises `ValueError: Unknown supermarket`.

## 2. Database schema

The live reconciliation tables are created in `SCHEMA_SQL` and extended by `_migrate_schema` for databases created before a column existed. `get_connection` turns on `PRAGMA foreign_keys = ON`.

### 2.1 Relationships

```text
product_categories 1───* product_subcategories 1───* products

supermarket_invoices 1───* invoice_categories
supermarket_invoices 1───* supermarket_payments
```

`invoice_categories` does not reference `products`. The line keeps its own copy of the names, pack size, price, quantity, and amount.

### 2.2 `product_categories`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE | Category name, such as Snacks or Dairy |

### 2.3 `product_subcategories`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `category_id` | INTEGER NOT NULL | FK to `product_categories.id`, `ON DELETE CASCADE` |
| `name` | TEXT NOT NULL | Unique together with `category_id` |

### 2.4 `products`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Not copied onto invoice lines |
| `subcategory_id` | INTEGER NOT NULL | FK to `product_subcategories.id`, `ON DELETE CASCADE` |
| `name` | TEXT NOT NULL | Unique together with `subcategory_id` |
| `pieces_per_pack` | REAL NOT NULL | Master pack size. `1` pack of `12` means 12 pieces |
| `price_per_piece` | REAL NOT NULL | Master price |

There is no tax-exempt column on the product. Tax exempt is chosen on the invoice line.

`save_product` inserts or updates this row and commits. It does not run an update against `invoice_categories`. `delete_product` deletes the product row only. Category and sub-category rows are not removed by that delete.

### 2.5 `supermarket_invoices`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `invoice_number` | TEXT UNIQUE | Required |
| `supermarket_name` | TEXT | `Capital`, `Makro`, or `One Stop Mart` |
| `shop` | TEXT | Must be one of that supermarket's shops |
| `invoice_date` | TEXT | ISO date |
| `due_date` | TEXT | ISO date. Must be on or after `invoice_date` |
| `total_invoice_amount` | REAL | Sum of line amounts after normalize |
| `is_tax_included` | INTEGER | 1 or 0. Capital is always 1 |
| `tax_treatment` | TEXT | `tax_included`, `tax_exclusive`, or `no_tax_no_retro` |
| `tax_percentage` | REAL | Fraction. Used by Makro |
| `retro_percentage` | REAL | Fraction. Makro stores the entered retro here |
| `discount_percentage` | REAL | Only `0`, `0.03`, `0.04`, or `0.05` |
| `remark` | TEXT | Discount sentence when a discount is chosen and the remark was empty |
| `credit_days` | INTEGER | Default 30. The screen sets due date from this value. A different due date can be saved |
| `category` | TEXT | Legacy single-category column. Cleared when lines exist |
| `quantity` | REAL | Legacy single quantity. Cleared when lines exist |
| `status` | TEXT | `Pending`, `Partial`, or `Paid` |

`_normalize_tax_treatment` sets Capital to `tax_included`. For Makro, tax above 0 is `tax_included`, tax 0 with retro above 0 is `tax_exclusive`, and both 0 is `no_tax_no_retro`. One Stop Mart is stored as `tax_exclusive` with tax and retro percents forced to 0 on the invoice. Its back margin is stored on the payment, in `retro_percentage`.

### 2.6 `invoice_categories`

This is the invoice-item table. One invoice has many rows.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `invoice_id` | INTEGER NOT NULL | FK to `supermarket_invoices.id`, `ON DELETE CASCADE` |
| `category` | TEXT | Copied name |
| `subcategory` | TEXT | Copied name |
| `product_name` | TEXT | Copied name. Empty on older free-text lines |
| `unit` | TEXT | `Piece` or `Pack` for product lines |
| `entered_quantity` | REAL | Quantity the clerk typed |
| `pieces_per_pack` | REAL | Pack size copied at save time |
| `price_per_piece` | REAL | Price copied at save time |
| `base_quantity` | REAL | Piece quantity after conversion |
| `quantity` | REAL | For product lines this is the same piece quantity as `base_quantity` |
| `amount` | REAL | Line amount at save time |
| `tax_exempt` | INTEGER | 1 when that line skips Capital's divide-by-105 step or Makro's tax |

Index: `idx_invoice_categories_invoice (invoice_id)`.

`_replace_categories` deletes the invoice's lines and inserts the current list. Create and update both go through that function, so an edit replaces the snapshot with the values on the form at that moment. A master-data edit does not call it.

### 2.7 `supermarket_payments`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `invoice_id` | INTEGER NOT NULL | FK to `supermarket_invoices.id`, `ON DELETE CASCADE` |
| `payment_date` | TEXT | Shown as Received date on the Makro report |
| `actual_received_amount` | REAL | Bank amount |
| `retro_percentage` | REAL | Capital retro, Makro retro, or One Stop Mart back margin |
| `delivery_charge_percentage` | REAL | Capital delivery, or One Stop Mart delivery incentive |
| `tax_percentage` | REAL | Makro tax entered with the payment |
| `retro_cut_off_date` | TEXT NULL | Makro only, optional |
| `created_at` | TEXT | SQLite `datetime('now')` |

Index: `idx_sm_payments_invoice (invoice_id)`.

After insert, `_refresh_status` sums received amounts and writes `status`:

- received about 0: `Pending`
- received below expected, beyond 0.01: `Partial`
- received at or above expected: `Paid`

### 2.8 Snapshot rule

Historical price and pack size live on the invoice line, not behind a foreign key.

When a product line is saved, `_city_mart_line` computes and stores:

- `pieces_per_pack` and `price_per_piece` from the values on the line, which the screen copied from the product at entry time
- `base_quantity`: entered quantity for Piece, or entered quantity times pieces per pack for Pack
- `amount`: entered quantity times the unit price. Pack unit price is price per piece times pieces per pack

Example: 2 packs, 12 pieces per pack, 10 per piece.

- `entered_quantity` = 2
- `pieces_per_pack` = 12
- `price_per_piece` = 10
- `base_quantity` = 24
- `amount` = 240

`save_product` then changes only `products.pieces_per_pack` and `products.price_per_piece`. The next read of the invoice uses `_categories_for`, which selects those copied columns. Reports sum `invoice_categories.amount` and `invoice_categories.base_quantity`. They do not join `products`.

A later invoice edit can store a new snapshot, because update rebuilds that invoice's lines from the form. Deleting a product does not delete or rewrite invoice lines.

### 2.9 Older tables in the same file

`clients`, `invoices`, `line_items`, `invoice_summary`, and `payments` belong to the earlier office workbook flow. `init_db` still creates them and seeds clients. The reconciliation screen does not read them. Supermarket profiles are not among them.

## 3. Calculation engine

### 3.1 Strategy pattern

`expected_payment.py` defines `PaymentStrategy` with one method, `calculate(terms) -> ExpectedPayment`. Three classes implement it:

| Class | `name` | Selected when |
|---|---|---|
| `CapitalStrategy` | `Capital` | `strategy_for("Capital")` |
| `MakroStrategy` | `Makro` | `strategy_for("Makro")` |
| `OneStopMartStrategy` | `One Stop Mart` | `strategy_for("One Stop Mart")` |

`expected_payment(supermarket_name, terms)` looks up the class in `_STRATEGIES` and calls `calculate`. The strategies do not query the database. `ReconciliationRepository.terms_for` builds the `PaymentTerms` they are allowed to see:

| Field | Source |
|---|---|
| `total_invoice_amount` | Invoice total, already the sum of line amounts |
| `discount_percentage` | Invoice |
| `exempt_amount` | Sum of `invoice_categories.amount` where `tax_exempt` is 1 |
| `retro_percentage` | Latest payment, or the Makro invoice value, or 3% before a Capital payment |
| `delivery_charge_percentage` | Latest Capital or One Stop Mart payment. 0 before that payment. Always 0 for Makro |
| `tax_percentage` | Makro invoice, which a Makro payment updates |
| `is_tax_included` | True for Capital |

`terms_for` uses `default_terms` when the invoice has no payment, and also whenever the supermarket is Makro. A Makro payment first copies its tax and retro onto the invoice, so the default still sees the rates just entered.

The exempt share is `min(exempt_amount, gross) / gross`, using the pre-discount invoice total as the denominator. That fraction splits the amount after discount. It is not a second product lookup.

### 3.2 Shared first step

Every strategy starts with the invoice total.

`Amount after discount = total × (1 − discount)`.

The discount is none, 3%, 4%, or 5%. It is applied before that supermarket's tax, retro, and delivery. If the remark is empty, the saved text is `Discount {percent}%: {deduction}`.

### 3.3 Capital

`CapitalStrategy.calculate`:

1. `net = total − discount`
2. `retro = net × retro_percentage`
3. `invoice_balance = net − retro`
4. Split the balance by the exempt fraction.
5. Taxable delivery = `((taxable balance / 105) × 100) × delivery %`
6. Tax-exempt delivery = `exempt balance × delivery %`
7. `expected = invoice balance − taxable delivery − exempt delivery`

Capital prices are treated as already including 5% tax. `capital_tax_included` returns true. The strategy does not add 5% on top. `is_tax_included=False` on the terms does not switch Capital onto a different formula. Only a tax-exempt line amount moves part of the balance onto step 6.

Before any payment, delivery is 0 and retro is 3%, so the expected amount is the discounted total times 0.97. The shop's 0%, 4%, or 6% delivery is applied when the payment is recorded. The clerk can change the retro percent on that payment, including for Hyper.

A fully taxable invoice uses only step 5. A fully exempt invoice uses only step 6. A mixed invoice uses both.

### 3.4 Makro

`MakroStrategy.calculate`:

1. `net = total − discount`
2. Split `net` into taxable and exempt shares.
3. `tax = taxable net × tax_percentage`
4. `grand total = taxable net + tax + exempt net`
5. `expected = grand total × (1 − retro_percentage)`

Makro has no divide-by-105 step. The usual values entered on the invoice are tax 5% and retro 5%. Both can be any percent from 0 to 100. Tax is added only to the taxable share. A fully exempt invoice keeps `grand total = net`, then retro is still taken off that grand total. Tax 0 and retro 0 leaves the expected payment equal to the amount after discount.

The Makro report adds Received date (`payment_date`) and Retro cut-off (`retro_cut_off_date`) from the latest payment.

### 3.5 One Stop Mart

`OneStopMartStrategy.calculate`:

1. `net = total × (1 − discount)`
2. `combined = back margin % + delivery incentive %`
3. `expected = net − (net × combined)`

No tax is added and nothing is divided by 105. The tax-exempt flag is stored on the line and is passed in `exempt_amount`, but this strategy does not read it. Back margin and delivery incentive are whatever percents were entered on the payment. They are not fixed at 5% + 5%. Before a payment both are 0, so the expected amount is the discounted invoice total.

### 3.6 Reports

`report_rows` loads each invoice, builds terms, runs the strategy, and returns invoice total, expected payment, received amount, variance (`received − expected`), status, and the calculation steps.

`city_category_summary` groups `invoice_categories` by category and sub-category and sums `amount` for the selected supermarket and shop.

`city_item_summary` groups by product name, category, and sub-category and sums `base_quantity` as pieces, plus amount. Potato chips and chocolate stay on separate rows because the group key includes the product name.

Both summaries read the snapshot columns. A master price change does not move their totals.
