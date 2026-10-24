"""Client metadata and default tax/retro percentages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientConfig:
    id: str
    name: str
    default_tax_pct: float
    default_retro_pct: float
    branches: tuple[str, ...]
    workbook_glob: str
    summary_sheet_prefix: str


CLIENTS: dict[str, ClientConfig] = {
    "capital": ClientConfig(
        id="capital",
        name="Capital",
        default_tax_pct=0.0,
        default_retro_pct=3.0,
        branches=("CREL", "DC MDY", "Hyper", "G&G"),
        workbook_glob="Capital*.xlsx",
        summary_sheet_prefix="Summary",
    ),
    "makro": ClientConfig(
        id="makro",
        name="Makro",
        default_tax_pct=5.0,
        default_retro_pct=5.0,
        branches=("Sat San", "Kha Wae Chan"),
        workbook_glob="Makro*.xlsx",
        summary_sheet_prefix="Summary",
    ),
    "one_stop_mart": ClientConfig(
        id="one_stop_mart",
        name="One Stop Mart",
        default_tax_pct=0.0,
        default_retro_pct=10.0,
        branches=("One Stop Mart",),
        workbook_glob="One Stop Mart*.xlsx",
        summary_sheet_prefix="Summary",
    ),
}


def get_client(client_id: str) -> ClientConfig:
    try:
        return CLIENTS[client_id]
    except KeyError as exc:
        raise KeyError(f"Unknown client_id: {client_id}") from exc


def all_clients() -> list[ClientConfig]:
    return list(CLIENTS.values())
