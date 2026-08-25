"""Live read of the onboarding Google Sheet.

Two ways to read it, picked automatically:

1. **Service account (secure, recommended).** Set GOOGLE_SERVICE_ACCOUNT_FILE
   (path to a JSON key) or GOOGLE_SERVICE_ACCOUNT_JSON (the key inline). Share the
   sheet with that service account's email as *Viewer* — the sheet stays private
   to the web, and the backend authenticates with the Google Sheets API.

2. **Public CSV (fallback).** If no service account is configured, we fetch the
   CSV export, which only works if the sheet is shared "Anyone with the link".

Sheet id / gid come from env and default to the team's sheet.
"""

import csv
import io
import json
import os
import re
from urllib.parse import quote

import requests

SHEET_ID = os.getenv("ONBOARDING_SHEET_ID", "1kPCayPmbLIsoGTsRzJVLEQO5Cwd9OTkw")
SHEET_GID = os.getenv("ONBOARDING_SHEET_GID", "800451510")
TIMEOUT = int(os.getenv("ONBOARDING_SHEET_TIMEOUT", "10"))

# Read + write: we read the sheet for the board and delete rows when a candidate
# is removed in-app. Writing needs a service account with Editor access to the
# sheet (the public-CSV fallback can only read).
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetError(Exception):
    """Raised when the sheet can't be fetched or parsed (surfaced as 502)."""


# ---------- shared parsing ----------
def _dedupe(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, h in enumerate(headers):
        name = (h or "").strip() or f"Column {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _matrix_to_payload(matrix: list[list[str]]) -> dict:
    """First row → columns, every non-empty following row → a record dict."""
    if not matrix:
        return {"columns": [], "rows": []}
    columns = _dedupe(matrix[0])
    rows = []
    for raw in matrix[1:]:
        if not any((c or "").strip() for c in raw):
            continue
        rows.append(
            {columns[i]: (raw[i] if i < len(raw) else "") for i in range(len(columns))}
        )
    return {"columns": columns, "rows": rows}


# ---------- secure path: service account + Sheets API ----------
def _credentials():
    """Build service-account creds from env, or None if not configured."""
    inline = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not inline and not (path and os.path.exists(path)):
        return None
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise SheetError(
            "google-auth isn't installed; run pip install -r requirements.txt."
        )
    if inline:
        return service_account.Credentials.from_service_account_info(
            json.loads(inline), scopes=_SCOPES
        )
    return service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)


def parse_sheet_url(url: str) -> tuple[str, str]:
    """Pull the spreadsheet id (and gid if present) out of a Google Sheets link.

    Accepts a full URL or a bare spreadsheet id. Raises SheetError if neither."""
    url = (url or "").strip()
    if not url:
        raise SheetError("Paste a Google Sheets link.")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9\-_]+)", url)
    sid = (
        m.group(1)
        if m
        else (url if re.fullmatch(r"[a-zA-Z0-9\-_]{20,}", url) else None)
    )
    if not sid:
        raise SheetError("That doesn't look like a Google Sheets link.")
    g = re.search(r"[#&?]gid=(\d+)", url)
    return sid, (g.group(1) if g else "")


def _fetch_via_api(creds, sid: str, gid: str) -> dict:
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds.refresh(GoogleAuthRequest())
    headers = {"Authorization": f"Bearer {creds.token}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"

    # Resolve the tab title for our gid (the values API addresses tabs by name).
    meta = requests.get(
        base,
        params={"fields": "sheets(properties(sheetId,title))"},
        headers=headers,
        timeout=TIMEOUT,
    )
    if meta.status_code in (401, 403):
        raise SheetError(
            "The service account can't open this sheet. In Google Sheets, share it "
            "with the service account's email (as Viewer)."
        )
    if meta.status_code != 200:
        raise SheetError(f"Google Sheets API error ({meta.status_code}).")

    sheets = meta.json().get("sheets", [])
    title = next(
        (
            s["properties"]["title"]
            for s in sheets
            if str(s["properties"].get("sheetId")) == str(gid)
        ),
        sheets[0]["properties"]["title"] if sheets else None,
    )
    if not title:
        return {"columns": [], "rows": []}

    vals = requests.get(
        f"{base}/values/{quote(title)}",
        headers=headers,
        timeout=TIMEOUT,
    )
    if vals.status_code != 200:
        raise SheetError(
            f"Google Sheets API error reading values ({vals.status_code})."
        )
    return _matrix_to_payload(vals.json().get("values", []))


# ---------- fallback path: public CSV export ----------
def _fetch_via_csv(sid: str, gid: str) -> dict:
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
    if gid:  # blank gid → let Google serve the first tab
        url += f"&gid={gid}"
    try:
        resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        raise SheetError(f"Couldn't reach Google Sheets: {e}")
    if resp.status_code != 200 or "text/csv" not in resp.headers.get(
        "content-type", ""
    ):
        raise SheetError(
            "The sheet isn't readable. Share it as 'Anyone with the link → Viewer', "
            "or configure a Google service account for private sheets."
        )
    text = resp.content.decode("utf-8", errors="replace")
    return _matrix_to_payload(list(csv.reader(io.StringIO(text))))


def fetch_rows(spreadsheet_id: str | None = None, gid: str | None = None) -> dict:
    """Return {"columns": [...], "rows": [{col: value}, ...]} from the sheet.

    Defaults to the env-configured sheet when no id is given."""
    sid = spreadsheet_id or SHEET_ID
    g = gid if gid is not None else SHEET_GID
    creds = _credentials()
    if creds is not None:
        return _fetch_via_api(creds, sid, g)
    return _fetch_via_csv(sid, g)


# ---------- write path: delete rows (candidate removed in-app) ----------
def _authed_headers(creds) -> dict:
    from google.auth.transport.requests import Request as GoogleAuthRequest

    creds.refresh(GoogleAuthRequest())
    return {"Authorization": f"Bearer {creds.token}"}


def _resolve_tab(base: str, headers: dict) -> tuple[str, int]:
    """Return (tab title, numeric sheetId) for our configured gid."""
    meta = requests.get(
        base,
        params={"fields": "sheets(properties(sheetId,title))"},
        headers=headers,
        timeout=TIMEOUT,
    )
    if meta.status_code in (401, 403):
        raise SheetError(
            "The service account can't edit this sheet. Share it with the service "
            "account's email as Editor."
        )
    if meta.status_code != 200:
        raise SheetError(f"Google Sheets API error ({meta.status_code}).")
    sheets = meta.json().get("sheets", [])
    if not sheets:
        raise SheetError("The spreadsheet has no tabs.")
    props = next(
        (
            s["properties"]
            for s in sheets
            if str(s["properties"].get("sheetId")) == str(SHEET_GID)
        ),
        sheets[0]["properties"],
    )
    return props["title"], int(props["sheetId"])


def delete_rows_by_email(emails: set[str]) -> int:
    """Delete every sheet row whose email column matches one of `emails`.

    Best-effort two-way sync: keeps a deleted-in-app candidate from being
    re-imported by the next sync. Requires a service account with Editor access;
    with only the public-CSV fallback (no creds) this is a no-op.
    """
    targets = {e.strip().lower() for e in emails if e and e.strip()}
    if not targets:
        return 0
    creds = _credentials()
    if creds is None:
        return 0  # can't write without a service account

    headers = _authed_headers(creds)
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"
    title, numeric_id = _resolve_tab(base, headers)

    vals = requests.get(
        f"{base}/values/{quote(title)}", headers=headers, timeout=TIMEOUT
    )
    if vals.status_code != 200:
        raise SheetError(
            f"Google Sheets API error reading values ({vals.status_code})."
        )
    values = vals.json().get("values", [])
    if not values:
        return 0

    header = values[0]
    email_idx = next(
        (i for i, h in enumerate(header) if "email" in (h or "").lower()), None
    )
    if email_idx is None:
        return 0

    # 0-based row indices (row 0 is the header) whose email matches.
    to_delete = [
        r
        for r in range(1, len(values))
        if (values[r][email_idx] if email_idx < len(values[r]) else "").strip().lower()
        in targets
    ]
    if not to_delete:
        return 0

    # Delete bottom-up so earlier indices stay valid as rows shift.
    body = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": numeric_id,
                        "dimension": "ROWS",
                        "startIndex": idx,
                        "endIndex": idx + 1,
                    }
                }
            }
            for idx in sorted(to_delete, reverse=True)
        ]
    }
    resp = requests.post(
        f"{base}:batchUpdate",
        headers=headers,
        json=body,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise SheetError(f"Google Sheets API error deleting rows ({resp.status_code}).")
    return len(to_delete)
