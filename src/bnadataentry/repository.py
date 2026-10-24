"""CRUD repository for invoices, line items, and summaries."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from bnadataentry.calculations import refresh_invoice
from bnadataentry.models import Invoice, InvoiceSummary, LineItem, Payment


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_to_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


class InvoiceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_invoice(self, invoice: Invoice, *, recompute: bool = True) -> Invoice:
        if recompute:
            refresh_invoice(invoice, use_stored_amounts=False)
        cur = self.conn.execute(
            """
            INSERT INTO invoices (
                client_id, invoice_no, po_no, po_date, invoice_date,
                due_date, branch, product_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice.client_id,
                invoice.invoice_no,
                invoice.po_no,
                _date_to_str(invoice.po_date),
                _date_to_str(invoice.invoice_date),
                _date_to_str(invoice.due_date),
                invoice.branch,
                invoice.product_label,
            ),
        )
        invoice.id = int(cur.lastrowid)
        self._insert_lines(invoice.id, invoice.lines)
        self._upsert_summary(invoice.id, invoice.summary)
        self.conn.commit()
        return invoice

    def upsert_invoice(self, invoice: Invoice, *, recompute: bool = True) -> Invoice:
        existing = self.find_by_key(
            invoice.client_id,
            invoice.invoice_no,
            invoice.invoice_date,
            branch=invoice.branch,
        )
        if existing is None:
            return self.create_invoice(invoice, recompute=recompute)
        invoice.id = existing.id
        return self.update_invoice(invoice, recompute=recompute)

    def update_invoice(self, invoice: Invoice, *, recompute: bool = True) -> Invoice:
        if invoice.id is None:
            raise ValueError("invoice.id is required for update")
        if recompute:
            refresh_invoice(invoice, use_stored_amounts=False)
        self.conn.execute(
            """
            UPDATE invoices SET
                po_no = ?, po_date = ?, invoice_date = ?, due_date = ?,
                branch = ?, product_label = ?, invoice_no = ?, client_id = ?
            WHERE id = ?
            """,
            (
                invoice.po_no,
                _date_to_str(invoice.po_date),
                _date_to_str(invoice.invoice_date),
                _date_to_str(invoice.due_date),
                invoice.branch,
                invoice.product_label,
                invoice.invoice_no,
                invoice.client_id,
                invoice.id,
            ),
        )
        self.conn.execute(
            "DELETE FROM line_items WHERE invoice_id = ?", (invoice.id,)
        )
        self._insert_lines(invoice.id, invoice.lines)
        self._upsert_summary(invoice.id, invoice.summary)
        self.conn.commit()
        return invoice

    def update_summary_overrides(
        self,
        invoice_id: int,
        *,
        tax_pct_override: float | None = None,
        retro_pct_override: float | None = None,
        remark: str | None = None,
        tax_amount: float | None = None,
        retro_amount: float | None = None,
        delivery_charge: float | None = None,
        balance_to_receive: float | None = None,
        branch_amounts: dict[str, float] | None = None,
        recompute: bool = True,
    ) -> Invoice:
        invoice = self.get_invoice(invoice_id)
        if invoice is None:
            raise KeyError(f"Invoice {invoice_id} not found")
        if tax_pct_override is not None:
            invoice.summary.tax_pct_override = tax_pct_override
        if retro_pct_override is not None:
            invoice.summary.retro_pct_override = retro_pct_override
        if remark is not None:
            invoice.summary.remark = remark
        if branch_amounts is not None:
            invoice.summary.branch_amounts = branch_amounts
        if tax_amount is not None:
            invoice.summary.tax_amount = tax_amount
        if retro_amount is not None:
            invoice.summary.retro_amount = retro_amount
        if delivery_charge is not None:
            invoice.summary.delivery_charge = abs(float(delivery_charge))
        if balance_to_receive is not None:
            invoice.summary.balance_to_receive = balance_to_receive

        if recompute and tax_amount is None and retro_amount is None:
            refresh_invoice(
                invoice,
                use_stored_amounts=delivery_charge is not None
                or balance_to_receive is not None,
            )
        self._upsert_summary(invoice.id, invoice.summary)  # type: ignore[arg-type]
        self.conn.commit()
        return invoice

    def delete_invoice(self, invoice_id: int) -> None:
        self.conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        self.conn.commit()

    def get_invoice(self, invoice_id: int) -> Invoice | None:
        row = self.conn.execute(
            "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_invoice(row)

    def find_by_key(
        self,
        client_id: str,
        invoice_no: str,
        invoice_date: date | None,
        *,
        branch: str | None = None,
    ) -> Invoice | None:
        sql = """
            SELECT * FROM invoices
            WHERE client_id = ? AND invoice_no = ?
              AND (
                (invoice_date IS NULL AND ? IS NULL)
                OR invoice_date = ?
              )
        """
        params: list[Any] = [
            client_id,
            invoice_no,
            _date_to_str(invoice_date),
            _date_to_str(invoice_date),
        ]
        if branch is not None:
            sql += " AND branch = ?"
            params.append(branch)
        sql += " LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return self._row_to_invoice(row)

    def list_invoices(
        self,
        *,
        client_id: str | None = None,
        branch: str | None = None,
        year: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        month: int | None = None,
    ) -> list[Invoice]:
        sql = "SELECT * FROM invoices WHERE 1=1"
        params: list[Any] = []
        if client_id:
            sql += " AND client_id = ?"
            params.append(client_id)
        if branch:
            sql += " AND branch = ?"
            params.append(branch)
        # Prefer explicit range; single `year` remains as exact match for older callers
        if year_from is not None or year_to is not None:
            if year_from is not None:
                sql += " AND CAST(strftime('%Y', invoice_date) AS INTEGER) >= ?"
                params.append(year_from)
            if year_to is not None:
                sql += " AND CAST(strftime('%Y', invoice_date) AS INTEGER) <= ?"
                params.append(year_to)
        elif year is not None:
            sql += " AND CAST(strftime('%Y', invoice_date) AS INTEGER) = ?"
            params.append(year)
        if month is not None:
            sql += " AND CAST(strftime('%m', invoice_date) AS INTEGER) = ?"
            params.append(month)
        sql += " ORDER BY branch, invoice_date, invoice_no"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_invoice(row) for row in rows]

    # --- Payments ---

    def create_payment(self, payment: Payment) -> Payment:
        if payment.invoice_id is None:
            raise ValueError("payment.invoice_id is required")
        if payment.amount is None or float(payment.amount) <= 0:
            raise ValueError("payment.amount must be greater than zero")
        if payment.payment_date is None:
            raise ValueError("payment.payment_date is required")
        cur = self.conn.execute(
            """
            INSERT INTO payments (
                invoice_id, payment_date, amount, method, reference, remark
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payment.invoice_id,
                _date_to_str(payment.payment_date),
                float(payment.amount),
                payment.method or "",
                payment.reference or "",
                payment.remark or "",
            ),
        )
        payment.id = int(cur.lastrowid)
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment.id,)
        ).fetchone()
        return self._row_to_payment(row)

    def update_payment(self, payment: Payment) -> Payment:
        if payment.id is None:
            raise ValueError("payment.id is required for update")
        if payment.amount is None or float(payment.amount) <= 0:
            raise ValueError("payment.amount must be greater than zero")
        self.conn.execute(
            """
            UPDATE payments SET
                payment_date = ?, amount = ?, method = ?,
                reference = ?, remark = ?
            WHERE id = ?
            """,
            (
                _date_to_str(payment.payment_date),
                float(payment.amount),
                payment.method or "",
                payment.reference or "",
                payment.remark or "",
                payment.id,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment.id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"payment {payment.id} not found")
        return self._row_to_payment(row)

    def delete_payment(self, payment_id: int) -> None:
        self.conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        self.conn.commit()

    def get_payment(self, payment_id: int) -> Payment | None:
        row = self.conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_payment(row)

    def list_payments(self, invoice_id: int) -> list[Payment]:
        rows = self.conn.execute(
            """
            SELECT * FROM payments
            WHERE invoice_id = ?
            ORDER BY payment_date, id
            """,
            (invoice_id,),
        ).fetchall()
        return [self._row_to_payment(row) for row in rows]

    def list_payments_for_invoices(
        self, invoice_ids: list[int]
    ) -> list[Payment]:
        if not invoice_ids:
            return []
        placeholders = ",".join("?" for _ in invoice_ids)
        rows = self.conn.execute(
            f"""
            SELECT * FROM payments
            WHERE invoice_id IN ({placeholders})
            ORDER BY payment_date, id
            """,
            invoice_ids,
        ).fetchall()
        return [self._row_to_payment(row) for row in rows]

    def sum_paid_by_invoice_ids(
        self, invoice_ids: list[int]
    ) -> dict[int, float]:
        if not invoice_ids:
            return {}
        placeholders = ",".join("?" for _ in invoice_ids)
        rows = self.conn.execute(
            f"""
            SELECT invoice_id, COALESCE(SUM(amount), 0) AS paid
            FROM payments
            WHERE invoice_id IN ({placeholders})
            GROUP BY invoice_id
            """,
            invoice_ids,
        ).fetchall()
        return {int(row["invoice_id"]): float(row["paid"]) for row in rows}

    def count_invoices(self, client_id: str | None = None) -> int:
        if client_id:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM invoices WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM invoices").fetchone()
        return int(row["n"])

    def count_line_items(self, client_id: str | None = None) -> int:
        if client_id:
            row = self.conn.execute(
                """
                SELECT COUNT(*) AS n FROM line_items li
                JOIN invoices i ON i.id = li.invoice_id
                WHERE i.client_id = ?
                """,
                (client_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM line_items"
            ).fetchone()
        return int(row["n"])

    def _insert_lines(self, invoice_id: int, lines: list[LineItem]) -> None:
        for line in lines:
            self.conn.execute(
                """
                INSERT INTO line_items (
                    invoice_id, product_description, pack_qty, pack_qty_fa,
                    list_price, line_total, discount_amount, tax_amount,
                    grand_total, is_foc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    line.product_description,
                    line.pack_qty,
                    line.pack_qty_fa,
                    line.list_price,
                    line.line_total or 0.0,
                    line.discount_amount,
                    line.tax_amount,
                    line.grand_total or 0.0,
                    1 if line.is_foc else 0,
                ),
            )

    def _upsert_summary(self, invoice_id: int, summary: InvoiceSummary) -> None:
        self.conn.execute(
            """
            INSERT INTO invoice_summary (
                invoice_id, branch_amounts_json, tax_amount, retro_amount,
                delivery_charge, balance_to_receive, remark,
                tax_pct_override, retro_pct_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                branch_amounts_json = excluded.branch_amounts_json,
                tax_amount = excluded.tax_amount,
                retro_amount = excluded.retro_amount,
                delivery_charge = excluded.delivery_charge,
                balance_to_receive = excluded.balance_to_receive,
                remark = excluded.remark,
                tax_pct_override = excluded.tax_pct_override,
                retro_pct_override = excluded.retro_pct_override
            """,
            (
                invoice_id,
                json.dumps(summary.branch_amounts),
                summary.tax_amount,
                summary.retro_amount,
                abs(float(summary.delivery_charge or 0.0)),
                summary.balance_to_receive,
                summary.remark,
                summary.tax_pct_override,
                summary.retro_pct_override,
            ),
        )

    def _row_to_invoice(self, row: sqlite3.Row) -> Invoice:
        invoice_id = int(row["id"])
        lines = [
            LineItem(
                id=int(lr["id"]),
                invoice_id=invoice_id,
                product_description=lr["product_description"],
                pack_qty=float(lr["pack_qty"]),
                pack_qty_fa=float(lr["pack_qty_fa"]) if "pack_qty_fa" in lr else 0.0,
                list_price=float(lr["list_price"]),
                line_total=float(lr["line_total"]),
                discount_amount=float(lr["discount_amount"]),
                tax_amount=float(lr["tax_amount"]),
                grand_total=float(lr["grand_total"]),
                is_foc=bool(lr["is_foc"]),
            )
            for lr in self.conn.execute(
                "SELECT * FROM line_items WHERE invoice_id = ? ORDER BY id",
                (invoice_id,),
            ).fetchall()
        ]
        summary_row = self.conn.execute(
            "SELECT * FROM invoice_summary WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        if summary_row:
            try:
                raw_dc = summary_row["delivery_charge"]
            except (KeyError, IndexError):
                raw_dc = None
            summary = InvoiceSummary(
                invoice_id=invoice_id,
                branch_amounts=json.loads(summary_row["branch_amounts_json"] or "{}"),
                tax_amount=summary_row["tax_amount"],
                retro_amount=summary_row["retro_amount"],
                delivery_charge=float(raw_dc if raw_dc is not None else 0.0),
                balance_to_receive=summary_row["balance_to_receive"],
                remark=summary_row["remark"] or "",
                tax_pct_override=summary_row["tax_pct_override"],
                retro_pct_override=summary_row["retro_pct_override"],
            )
        else:
            summary = InvoiceSummary(invoice_id=invoice_id)

        return Invoice(
            id=invoice_id,
            client_id=row["client_id"],
            invoice_no=row["invoice_no"],
            po_no=row["po_no"] or "",
            po_date=_str_to_date(row["po_date"]),
            invoice_date=_str_to_date(row["invoice_date"]),
            due_date=_str_to_date(row["due_date"]),
            branch=row["branch"] or "",
            product_label=row["product_label"] or "",
            lines=lines,
            summary=summary,
        )

    def _row_to_payment(self, row: sqlite3.Row) -> Payment:
        return Payment(
            id=int(row["id"]),
            invoice_id=int(row["invoice_id"]),
            payment_date=_str_to_date(row["payment_date"])
            or date.fromisoformat("1970-01-01"),
            amount=float(row["amount"]),
            method=row["method"] or "",
            reference=row["reference"] or "",
            remark=row["remark"] or "",
            created_at=row["created_at"],
        )
