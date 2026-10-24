"""SQLite connection, schema migrations, and client seeding."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bnadataentry.clients import all_clients

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "app.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    default_tax_pct REAL NOT NULL DEFAULT 0,
    default_retro_pct REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    invoice_no TEXT NOT NULL,
    po_no TEXT NOT NULL DEFAULT '',
    po_date TEXT,
    invoice_date TEXT,
    due_date TEXT,
    branch TEXT NOT NULL DEFAULT '',
    product_label TEXT NOT NULL DEFAULT '',
    UNIQUE (client_id, invoice_no, invoice_date, branch)
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_description TEXT NOT NULL,
    pack_qty REAL NOT NULL DEFAULT 0,
    pack_qty_fa REAL NOT NULL DEFAULT 0,
    list_price REAL NOT NULL DEFAULT 0,
    line_total REAL NOT NULL DEFAULT 0,
    discount_amount REAL NOT NULL DEFAULT 0,
    tax_amount REAL NOT NULL DEFAULT 0,
    grand_total REAL NOT NULL DEFAULT 0,
    is_foc INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoice_summary (
    invoice_id INTEGER PRIMARY KEY REFERENCES invoices(id) ON DELETE CASCADE,
    branch_amounts_json TEXT NOT NULL DEFAULT '{}',
    tax_amount REAL,
    retro_amount REAL,
    delivery_charge REAL NOT NULL DEFAULT 0,
    balance_to_receive REAL,
    remark TEXT NOT NULL DEFAULT '',
    tax_pct_override REAL,
    retro_pct_override REAL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    payment_date TEXT NOT NULL,
    amount REAL NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date);

CREATE TABLE IF NOT EXISTS supermarket_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    supermarket_name TEXT NOT NULL,
    shop TEXT NOT NULL DEFAULT '',
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    total_invoice_amount REAL NOT NULL,
    is_tax_included INTEGER NOT NULL DEFAULT 0,
    tax_treatment TEXT NOT NULL DEFAULT '',
    tax_percentage REAL NOT NULL DEFAULT 0,
    retro_percentage REAL NOT NULL DEFAULT 0,
    discount_percentage REAL NOT NULL DEFAULT 0,
    remark TEXT NOT NULL DEFAULT '',
    credit_days INTEGER NOT NULL DEFAULT 30,
    category TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Pending'
);

CREATE TABLE IF NOT EXISTS supermarket_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES supermarket_invoices(id) ON DELETE CASCADE,
    payment_date TEXT NOT NULL,
    actual_received_amount REAL NOT NULL,
    retro_percentage REAL NOT NULL DEFAULT 0,
    delivery_charge_percentage REAL NOT NULL DEFAULT 0,
    tax_percentage REAL NOT NULL DEFAULT 0,
    retro_cut_off_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sm_payments_invoice
    ON supermarket_payments(invoice_id);

CREATE TABLE IF NOT EXISTS invoice_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES supermarket_invoices(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    subcategory TEXT NOT NULL DEFAULT '',
    product_name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    entered_quantity REAL NOT NULL DEFAULT 0,
    pieces_per_pack REAL NOT NULL DEFAULT 0,
    price_per_piece REAL NOT NULL DEFAULT 0,
    base_quantity REAL NOT NULL DEFAULT 0,
    tax_exempt INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_invoice_categories_invoice
    ON invoice_categories(invoice_id);

CREATE TABLE IF NOT EXISTS product_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_subcategories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES product_categories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    UNIQUE (category_id, name)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subcategory_id INTEGER NOT NULL REFERENCES product_subcategories(id)
        ON DELETE CASCADE,
    name TEXT NOT NULL,
    pieces_per_pack REAL NOT NULL,
    price_per_piece REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (subcategory_id, name)
);
"""


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit may reuse cached resources across threads.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create schema and seed clients. Returns an open connection."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    _seed_clients(conn)
    conn.commit()
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply lightweight column additions for existing DBs."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(line_items)").fetchall()
    }
    if "pack_qty_fa" not in cols:
        conn.execute(
            "ALTER TABLE line_items ADD COLUMN pack_qty_fa REAL NOT NULL DEFAULT 0"
        )
    _migrate_invoice_unique_include_branch(conn)
    _ensure_payments_table(conn)
    _ensure_delivery_charge_column(conn)
    _ensure_supermarket_shop_column(conn)
    _ensure_tax_treatment_column(conn)
    _ensure_makro_rate_columns(conn)
    _ensure_invoice_category_columns(conn)
    _ensure_invoice_category_lines(conn)
    _ensure_commercial_columns(conn)
    _ensure_product_tables(conn)
    _ensure_product_is_active(conn)


def _ensure_supermarket_shop_column(conn: sqlite3.Connection) -> None:
    """Add shop to supermarket invoices on databases created before shops existed."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "supermarket_invoices" not in tables:
        return
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_invoices)").fetchall()
    }
    if "shop" not in cols:
        conn.execute(
            "ALTER TABLE supermarket_invoices ADD COLUMN shop TEXT NOT NULL DEFAULT ''"
        )


def _ensure_tax_treatment_column(conn: sqlite3.Connection) -> None:
    """Store Capital and Makro tax choices on databases created before them."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "supermarket_invoices" not in tables:
        return
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_invoices)").fetchall()
    }
    if "tax_treatment" in cols:
        return
    conn.execute(
        "ALTER TABLE supermarket_invoices "
        "ADD COLUMN tax_treatment TEXT NOT NULL DEFAULT ''"
    )
    conn.execute(
        """
        UPDATE supermarket_invoices
        SET tax_treatment = CASE
            WHEN supermarket_name = 'Makro' THEN 'tax_included'
            WHEN is_tax_included = 1 THEN 'tax_included'
            ELSE 'tax_exclusive'
        END
        """
    )


def _ensure_makro_rate_columns(conn: sqlite3.Connection) -> None:
    """Store the Makro tax and retro percents the user chooses."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "supermarket_invoices" not in tables:
        return
    invoice_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_invoices)").fetchall()
    }
    added_invoice_rate = False
    if "tax_percentage" not in invoice_cols:
        conn.execute(
            "ALTER TABLE supermarket_invoices "
            "ADD COLUMN tax_percentage REAL NOT NULL DEFAULT 0"
        )
        added_invoice_rate = True
    if "retro_percentage" not in invoice_cols:
        conn.execute(
            "ALTER TABLE supermarket_invoices "
            "ADD COLUMN retro_percentage REAL NOT NULL DEFAULT 0"
        )
        added_invoice_rate = True
    if added_invoice_rate:
        conn.execute(
            """
            UPDATE supermarket_invoices
            SET tax_percentage = CASE
                    WHEN supermarket_name = 'Makro'
                         AND tax_treatment = 'tax_included' THEN 0.05
                    ELSE 0
                END,
                retro_percentage = CASE
                    WHEN supermarket_name = 'Makro'
                         AND tax_treatment != 'no_tax_no_retro' THEN 0.05
                    ELSE 0
                END
            """
        )
    if "supermarket_payments" not in tables:
        return
    payment_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_payments)").fetchall()
    }
    if "tax_percentage" not in payment_cols:
        conn.execute(
            "ALTER TABLE supermarket_payments "
            "ADD COLUMN tax_percentage REAL NOT NULL DEFAULT 0"
        )


def _ensure_invoice_category_columns(conn: sqlite3.Connection) -> None:
    """Store a product category and quantity on each reconciliation invoice."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "supermarket_invoices" not in tables:
        return
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(supermarket_invoices)").fetchall()
    }
    if "category" not in cols:
        conn.execute(
            "ALTER TABLE supermarket_invoices "
            "ADD COLUMN category TEXT NOT NULL DEFAULT ''"
        )
    if "quantity" not in cols:
        conn.execute(
            "ALTER TABLE supermarket_invoices "
            "ADD COLUMN quantity REAL NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        UPDATE supermarket_invoices
        SET is_tax_included = 1, tax_treatment = 'tax_included'
        WHERE supermarket_name = 'Capital' AND tax_treatment != 'tax_included'
        """
    )


def _ensure_invoice_category_lines(conn: sqlite3.Connection) -> None:
    """One invoice can have several category lines.

    Older invoices stored a single category on the invoice row. Those rows
    become one line, and the line amount is the invoice total.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES supermarket_invoices(id)
                ON DELETE CASCADE,
            category TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_invoice_categories_invoice
            ON invoice_categories(invoice_id)
        """
    )
    conn.execute(
        """
        INSERT INTO invoice_categories (invoice_id, category, quantity, amount)
        SELECT id, TRIM(category), quantity, total_invoice_amount
        FROM supermarket_invoices
        WHERE TRIM(category) != ''
          AND id NOT IN (SELECT invoice_id FROM invoice_categories)
        """
    )
    conn.execute(
        """
        UPDATE supermarket_invoices
        SET category = '', quantity = 0
        WHERE id IN (SELECT invoice_id FROM invoice_categories)
        """
    )


def _add_column(
    conn: sqlite3.Connection, table: str, name: str, declaration: str
) -> None:
    cols = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")


def _ensure_commercial_columns(conn: sqlite3.Connection) -> None:
    """Discount, remark, credit days, and Capital product-line snapshots."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "supermarket_invoices" not in tables:
        return
    _add_column(
        conn,
        "supermarket_invoices",
        "discount_percentage",
        "discount_percentage REAL NOT NULL DEFAULT 0",
    )
    _add_column(
        conn,
        "supermarket_invoices",
        "remark",
        "remark TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        conn,
        "supermarket_invoices",
        "credit_days",
        "credit_days INTEGER NOT NULL DEFAULT 30",
    )
    if "invoice_categories" not in tables:
        return
    for name, declaration in (
        ("subcategory", "subcategory TEXT NOT NULL DEFAULT ''"),
        ("product_name", "product_name TEXT NOT NULL DEFAULT ''"),
        ("unit", "unit TEXT NOT NULL DEFAULT ''"),
        ("entered_quantity", "entered_quantity REAL NOT NULL DEFAULT 0"),
        ("pieces_per_pack", "pieces_per_pack REAL NOT NULL DEFAULT 0"),
        ("price_per_piece", "price_per_piece REAL NOT NULL DEFAULT 0"),
        ("base_quantity", "base_quantity REAL NOT NULL DEFAULT 0"),
        ("tax_exempt", "tax_exempt INTEGER NOT NULL DEFAULT 0"),
    ):
        _add_column(conn, "invoice_categories", name, declaration)


def _ensure_product_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_subcategories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES product_categories(id)
                ON DELETE CASCADE,
            name TEXT NOT NULL,
            UNIQUE (category_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subcategory_id INTEGER NOT NULL REFERENCES product_subcategories(id)
                ON DELETE CASCADE,
            name TEXT NOT NULL,
            pieces_per_pack REAL NOT NULL,
            price_per_piece REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            UNIQUE (subcategory_id, name)
        )
        """
    )


def _ensure_product_is_active(conn: sqlite3.Connection) -> None:
    """Soft-delete flag on products created before is_active existed."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "products" not in tables:
        return
    _add_column(
        conn, "products", "is_active", "is_active INTEGER NOT NULL DEFAULT 1"
    )


def _ensure_delivery_charge_column(conn: sqlite3.Connection) -> None:
    """Add delivery_charge to invoice_summary on existing databases."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(invoice_summary)").fetchall()
    }
    if "delivery_charge" not in cols:
        conn.execute(
            "ALTER TABLE invoice_summary "
            "ADD COLUMN delivery_charge REAL NOT NULL DEFAULT 0"
        )


def _ensure_payments_table(conn: sqlite3.Connection) -> None:
    """Create payments table + indexes on existing databases."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            payment_date TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '',
            remark TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);
        CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date);
        """
    )


def _migrate_invoice_unique_include_branch(conn: sqlite3.Connection) -> None:
    """Recreate invoices so uniqueness is per branch (detail sheet), not Summary."""
    indexes = conn.execute("PRAGMA index_list(invoices)").fetchall()
    needs = False
    for idx in indexes:
        if not idx["unique"]:
            continue
        info = conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
        cols = [row["name"] for row in info]
        if cols == ["client_id", "invoice_no", "invoice_date"]:
            needs = True
            break
    # Fresh DBs already have the new UNIQUE including branch.
    if not needs:
        # Also rebuild if unique index somehow missing branch on older copies
        # created before this migration with the new SCHEMA_SQL already applied.
        return

    conn.executescript(
        """
        CREATE TABLE invoices_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL REFERENCES clients(id),
            invoice_no TEXT NOT NULL,
            po_no TEXT NOT NULL DEFAULT '',
            po_date TEXT,
            invoice_date TEXT,
            due_date TEXT,
            branch TEXT NOT NULL DEFAULT '',
            product_label TEXT NOT NULL DEFAULT '',
            UNIQUE (client_id, invoice_no, invoice_date, branch)
        );
        INSERT INTO invoices_new
            (id, client_id, invoice_no, po_no, po_date, invoice_date,
             due_date, branch, product_label)
        SELECT id, client_id, invoice_no, po_no, po_date, invoice_date,
               due_date, branch, product_label
        FROM invoices;
        DROP TABLE invoices;
        ALTER TABLE invoices_new RENAME TO invoices;
        """
    )


def _seed_clients(conn: sqlite3.Connection) -> None:
    for client in all_clients():
        conn.execute(
            """
            INSERT INTO clients (id, name, default_tax_pct, default_retro_pct)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                default_tax_pct = excluded.default_tax_pct,
                default_retro_pct = excluded.default_retro_pct
            """,
            (
                client.id,
                client.name,
                client.default_tax_pct,
                client.default_retro_pct,
            ),
        )


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
