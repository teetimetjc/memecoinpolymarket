"""Minimal Google Sheets append via a service account (no gspread dependency).

Credentials come only from the GOOGLE_SERVICE_ACCOUNT_JSON environment
variable (a GitHub secret). Nothing is ever read from or written to a file.
"""
import json
import os

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SHEET_ID = "14ynyoyb4Z2y2doDo_mosyopsvYP_vyvZL9TLYCw72Y0"  # "Meme Coin - Polymarket"
BASE = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"


def _headers():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    creds.refresh(Request())
    return {"Authorization": f"Bearer {creds.token}"}


def _ensure_tab(h, tab, header):
    meta = requests.get(BASE, headers=h, params={"fields": "sheets.properties.title"}, timeout=30)
    meta.raise_for_status()
    if tab in {s["properties"]["title"] for s in meta.json()["sheets"]}:
        return
    # Small grid: empty cells count toward the 10M-cell cap.
    req = {"requests": [{"addSheet": {"properties": {"title": tab, "gridProperties": {
        "rowCount": 2, "columnCount": len(header)}}}}]}
    requests.post(f"{BASE}:batchUpdate", headers=h, json=req, timeout=30).raise_for_status()
    _append(h, tab, [header])


def _append(h, tab, rows):
    r = requests.post(f"{BASE}/values/{tab}!A1:append", headers=h, json={"values": rows},
                      params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, timeout=60)
    r.raise_for_status()


def append(tab, header, rows):
    h = _headers()
    _ensure_tab(h, tab, header)
    if rows:
        _append(h, tab, rows)
    print(f"sheet: appended {len(rows)} rows to {tab!r}")
