# BNA Data Entry

Streamlit app for client invoice data entry, Excel import/export, and payments.
**SQLite** is the source of truth.

Based on the same structure as the office data-entry app, packaged as `bnadataentry`.

## Setup

```bash
cd /home/rangoondiscount/bnadataentry
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run

```bash
streamlit run app.py --server.port 8504 --server.address 0.0.0.0
```

Open **http://localhost:8504** (or your LAN IP on port **8504**).

## Layout

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit UI |
| `src/bnadataentry/` | Domain logic (DB, Excel, payments) |
| `data/` | SQLite database |
| `excel_data/` | Uploaded / source Excel workbooks |
| `exports/` | Exported workbooks |
| `tests/` | pytest suite |

## Clients

Capital · Makro · One Stop Mart (same templates as the office app until customized for BNA).
