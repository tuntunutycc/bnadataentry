"""Smoke tests for actions used by the Streamlit UI."""

from __future__ import annotations

from pathlib import Path

from bnadataentry.db import PROJECT_ROOT, init_db
from bnadataentry.excel_import import import_uploaded_workbooks
from bnadataentry.repository import InvoiceRepository


def test_import_uploaded_rejects_xls_without_samples(tmp_path: Path) -> None:
    """Legacy .xls uploads are rejected clearly."""
    conn = init_db(tmp_path / "xls.db")
    repo = InvoiceRepository(conn)
    bad = import_uploaded_workbooks(
        repo,
        [("Makro old.xls", b"not-a-real-workbook")],
        staging_dir=tmp_path / "staging",
    )
    assert bad[0].invoices_upserted == 0
    assert any(".xls" in e.message for e in bad[0].errors)


def test_app_module_defines_pages() -> None:
    import importlib.util
    import sys

    app_path = PROJECT_ROOT / "app.py"
    spec = importlib.util.spec_from_file_location("office_app", app_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["office_app"] = module
    spec.loader.exec_module(module)

    assert hasattr(module, "render_browse")
    assert hasattr(module, "render_edit")
    assert hasattr(module, "render_import_export")
