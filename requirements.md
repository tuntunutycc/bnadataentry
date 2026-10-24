# Supermarket Invoice & Payment Reconciliation System

Version 2. The invoice rules in [requirements-v2.pdf](requirements-v2.pdf) apply to Capital, Makro, and One Stop Mart. Each supermarket keeps its own payment formula. The invoice total is the shared starting amount.

The goal is to track invoices issued to three supermarkets (Capital, Makro, and One Stop Mart), record actual payments received, calculate expected payments based on specific business rules, and generate a summary report comparing expected vs. actual received amounts.

## 1. Data models

The system needs two main entities: Invoice and Payment.

### A. Invoice

Used to record the initial invoice sent to the supermarket.

| Field | Type | Notes |
|---|---|---|
| `invoice_number` | String, unique | |
| `supermarket_name` | Enum | `Capital`, `Makro`, `One Stop Mart` |
| `shop` | Enum, depends on supermarket | From the Excel shop sheets. Capital: CREL Distribution Center, DC MDY, G&G, Hyper. Makro: Makro-Sat Sun, Makro - Kha Wei Chan. One Stop Mart: One Stop Mart. Capital delivery follows the shop: Hyper 0%, DC MDY 6%, CREL and G&G 4%. |
| `invoice_date` | Date | |
| `due_date` | Date | |
| `total_invoice_amount` | Float | The total amount on the physical invoice. |
| `categories` | List | Every supermarket chooses lines from the same product list. Each line stores a snapshot of the product, unit, pack size, price, piece quantity, amount, and a tax-exempt flag. |
| `is_tax_included` | Boolean | Always true for Capital, because every Capital item price already includes 5% tax. For Makro it is true when the tax percent is above 0. |
| `tax_treatment` | Enum | Capital is always `tax_included`. For Makro this follows the percents: tax above 0% is tax included, tax 0% with retro is tax exclusive, and both 0% is no tax and no retro. |
| `tax_percentage` | Float | Makro only. The tax percent the user enters. `0.05` means 5%. |
| `retro_percentage` | Float | Makro only, on the invoice. The retro percent the user enters. `0.05` means 5%. |
| `discount_percentage` | Float | None, 3%, 4%, or 5% on every supermarket, taken off the invoice total before that supermarket's tax, retro, and delivery. |
| `remark` | String | The discount deduction is written here and can be edited. |
| `credit_days` | Integer | Due date starts as invoice date plus these days. The usual value is 30. The due date can still be changed by hand. |
| `status` | Enum | `Pending`, `Partial`, `Paid` |

### B. Payment

Used to record the actual money received and the deductions applied during the payment stage.

| Field | Type | Notes |
|---|---|---|
| `invoice_id` | Foreign key | Links to Invoice. |
| `payment_date` | Date | |
| `actual_received_amount` | Float | The actual amount transferred to the bank. |
| `retro_percentage` | Float | Capital retro is entered on the payment. The usual value is 3%, and it can be changed. Makro retro is the percent entered for that payment. One Stop Mart uses this for Back Margin. |
| `tax_percentage` | Float | Makro tax percent entered for that payment. |
| `delivery_charge_percentage` | Float | For example 0%, 4%, 6% for Capital, or Delivery Incentive for One Stop Mart. |
| `retro_cut_off_date` | Date, optional | For Makro tracking. |

## 2. Core features

### Feature 1: Invoice entry

* A form to input new invoices.
* The user must select the supermarket name, which determines how calculations are handled later.
* One product list is shared by Capital, Makro, and One Stop Mart: category, sub-category, product name, pieces per pack, and price per piece. Products sits in the left pane. Adding or deleting a product requires a username and password. Choosing a product on an invoice does not.
* An invoice line does not accept typed category names. It chooses Category, then Sub-category, then Product, then quantity, then Piece or Pack. A line can be marked tax exempt. Tab order is category, sub-category, product, quantity, unit, then add line.
* Each line stores a snapshot of the entered quantity, unit, pieces per pack, price per piece, piece quantity, and amount. A later price or pack-size change does not rewrite old invoices.
* Piece quantity is the entered quantity. Pack quantity is the entered quantity times pieces per pack. Example: 2 packs at 12 pieces per pack is stored and reported as 24 pieces.
* Line amount is the entered quantity times the price of the chosen unit. Pack price is price per piece times pieces per pack. The invoice total is the sum of the line amounts.
* Discount is none, 3%, 4%, or 5% on every supermarket. It is taken off that invoice total first. The deduction is written in the remark, which can be edited.
* Credit days set the due date from the invoice date. The usual value is 30 days. The due date can be overridden.
* A saved invoice can be edited or deleted. Delete removes that invoice, its payments, and its category lines.

### Feature 2: Payment entry

When adding a payment for an invoice, the system calculates the Expected Final Payment from that supermarket's formula and cross-checks it with the Actual Received Amount.

Capital delivery and the Capital retro percent are applied when a payment is recorded. Before any payment, Capital expected payment is the invoice balance after the usual 3% retro. Makro expected payment uses the tax and retro percents saved on the invoice.

**Capital**

Retro % is chosen on the payment, the same way as the delivery charge. The usual value is 3%. It can be a different percent, including for Hyper. Use 0% when retro does not apply.

`AmountAfterDiscount = total_invoice_amount * (1 - discount_percentage)`.

`InvoiceBalance = AmountAfterDiscount * (1 - retro_percentage)`.

Delivery is 0% for Hyper, 6% for DC MDY, and 4% for CREL Distribution Center and G&G. The shop supplies that default on the payment form.

Every Capital item price already includes 5% tax, so delivery uses the tax-included path unless the goods are tax exempt. There is no tax-included checkbox. A category row can be marked tax exempt.

The invoice balance is split by the tax-exempt line amounts. Any part of the invoice total that is not covered by those lines stays taxable.

* Taxable share: `BaseValue = (TaxableBalance / 105) * 100`. Delivery is `BaseValue * delivery_charge_percentage`.
* Tax-exempt share: delivery is `ExemptBalance * delivery_charge_percentage`, with no division by 105.

`Expected Final Payment = InvoiceBalance - taxable delivery - exempt delivery`.

When nothing is tax exempt and the discount is 0%, this is the previous Capital result. The delivery amount is then subtracted from the invoice balance.

`AmountAfterDiscount = total_invoice_amount * (1 - discount_percentage)` is the starting amount for every supermarket.

**Makro**

Tax % and Retro % are entered by the user. They are not fixed. The usual starting values are 5% and 5%. Use 0% when that charge does not apply. Makro has no divide-by-105 step.

The amount after discount is split by the tax-exempt line amounts. Tax is added only to the taxable share. The exempt share, such as dairy, is not increased by the tax percent.

`Tax = TaxableNet * tax_percentage`.

`GrandTotal = TaxableNet + Tax + ExemptNet`.

`Expected Final Payment = GrandTotal * (1 - retro_percentage)`.

With no discount and no exempt lines, this is the previous Makro result. A tax of 0% and a retro of 0% leaves the expected payment equal to the amount after discount. Received date and retro cut-off date are tracking fields and appear on the Makro report, together with the tax and retro percents.

**One Stop Mart**

Item prices already include tax, so the formula does not add 5% and has no divide-by-105 step. The tax-exempt checkbox is saved on the line and does not change this payment. Back margin and delivery incentive are entered on the payment and can be any percent. They are taken off the amount after the 3%, 4%, or 5% discount.

`TotalDiscountPercentage = retro_percentage + delivery_charge_percentage`.

`Expected Final Payment = AmountAfterDiscount - (AmountAfterDiscount * TotalDiscountPercentage)`.

With no 3/4/5% discount, this is the previous One Stop Mart result.

### Feature 3: Summary and reconciliation report

The reconciliation report table has these columns:

1. Invoice No., Supermarket, and Shop
2. Total Invoice Amount (original)
3. Expected Final Payment (calculated from the business rules)
4. Actual Received Amount (entered from the bank or payment slip)
5. Variance / Difference: `Actual Received Amount - Expected Final Payment`
6. Status (`Pending`, `Partial`, or `Paid`)
7. Makro only: Received Date and Retro cut-off date

Every supermarket uses the same two category reports for the selected shop:

1. Category view: group by category and sub-category and show total amount only.
2. Item view: group by product and show quantity in pieces. Potato chips and chocolate are not added into one quantity.

Invoices with no product lines are counted separately. Payment calculations still use the invoice total.

### Version 2 checks

* Capital: 2 packs, with 12 pieces per pack, is reported as 24 pieces, and the line amount uses the pack price. Expected payment then follows the Capital discount, retro, and delivery rules.
* Capital: choosing an invoice date and 7 credit days sets the due date 7 days later. That due date can still be changed by hand.
* Capital: a later change to a product's price or pieces per pack leaves invoices already saved unchanged.
* Capital: a discount of 3%, 4%, or 5% is taken off the invoice total before retro and delivery, and the deduction is written in the remark.
* Capital: a tax-exempt row, such as dairy, skips the divide-by-105 delivery step. An invoice with no exempt rows and no discount keeps the previous Capital result.
* Capital: a saved invoice can be edited, and it can be deleted without removing other invoices.
* Makro: a 3% discount is taken off before tax and retro. A tax-exempt share skips the tax percent. With no discount and no exempt lines, the previous Makro result stays the same.
* One Stop Mart: a 3%, 4%, or 5% discount is taken off before back margin and delivery incentive. With no such discount, the previous One Stop Mart result stays the same.

## 3. Calculation structure

Payment calculation is modular. Each supermarket has its own strategy so a rule can be updated without changing the others.
