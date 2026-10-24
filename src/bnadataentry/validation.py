"""Parsing and validation helpers for dates, numbers, and FOC values."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_FOC_TOKENS = {"foc", "f.o.c", "free", "free of charge"}


def parse_date(value: Any) -> date | None:
    """Parse Excel-ish dates (d.m.yyyy, datetime, date). Empty → None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {value!r}")


def is_foc_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in _FOC_TOKENS


def parse_number(value: Any, *, allow_foc: bool = False) -> float:
    """Coerce a cell value to float. FOC → 0 when allow_foc is True."""
    if value is None or value == "":
        return 0.0
    # pandas / numpy empty cells become NA or NaN after DataFrame ffill paths
    try:
        if value != value:  # NaN
            return 0.0
    except (TypeError, ValueError):
        pass
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    # pd.NA / NAType
    if type(value).__name__ in {"NAType", "NaTType"}:
        return 0.0
    if allow_foc and is_foc_value(value):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text.lower() in {"<na>", "nan", "none", "nat"}:
        return 0.0
    if allow_foc and is_foc_value(text):
        return 0.0
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Unrecognized number: {value!r}") from exc


def normalize_invoice_no(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    # Preserve leading zeros from Excel text cells; strip trailing .0 from floats
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def require_non_empty(value: Any, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def is_total_row(value: Any) -> bool:
    """True for Excel subtotal / total footer labels (not product lines)."""
    if value is None:
        return False
    text = str(value).strip().lower().replace("  ", " ")
    if not text:
        return False
    return (
        text == "total"
        or text.startswith("total ")
        or text.startswith("total amount")
        or text == "grand total"
        or text.startswith("grand total")
        or text in {"subtotal", "sub total", "sub-total"}
    )
