"""Database bootstrap and schema tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bnadataentry.db import init_db, list_tables


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_init_db_creates_tables(db_path: Path) -> None:
    conn = init_db(db_path)
    tables = list_tables(conn)
    assert "clients" in tables
    assert "invoices" in tables
    assert "line_items" in tables
    assert "invoice_summary" in tables
    assert "payments" in tables
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(invoice_summary)").fetchall()
    }
    assert "delivery_charge" in cols
    conn.close()


def test_clients_are_seeded(db_path: Path) -> None:
    conn = init_db(db_path)
    rows = conn.execute(
        "SELECT id, name FROM clients ORDER BY id"
    ).fetchall()
    ids = [row["id"] for row in rows]
    assert ids == ["capital", "makro", "one_stop_mart"]
    names = {row["id"]: row["name"] for row in rows}
    assert names["capital"] == "Capital"
    assert names["makro"] == "Makro"
    assert names["one_stop_mart"] == "One Stop Mart"
    conn.close()


def test_unique_invoice_constraint(db_path: Path) -> None:
    conn = init_db(db_path)
    conn.execute(
        """
        INSERT INTO invoices (client_id, invoice_no, invoice_date, branch)
        VALUES ('capital', '1028', '2026-07-21', 'CREL')
        """
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO invoices (client_id, invoice_no, invoice_date, branch)
            VALUES ('capital', '1028', '2026-07-21', 'CREL')
            """
        )
    conn.close()


def test_init_db_is_idempotent(db_path: Path) -> None:
    conn1 = init_db(db_path)
    conn1.close()
    conn2 = init_db(db_path)
    count = conn2.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    assert count == 3
    conn2.close()
