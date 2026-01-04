from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import Settings
from app.utils.time import today_jst_ymd
from app.utils.errors import ExternalApiError


@dataclass
class PlannedRow:
    keyword: str
    planned_date: str


class SheetsClient:
    def __init__(self, settings: Settings):
        self._spreadsheet_id = settings.sheets_spreadsheet_id
        self._worksheet = settings.sheets_worksheet_name
        self._h_keyword = (settings.sheets_header_keyword or "").strip()
        self._h_planned_date = (settings.sheets_header_planned_date or "").strip()

        if not self._spreadsheet_id:
            raise RuntimeError("Missing SHEETS_SPREADSHEET_ID")
        if not self._worksheet:
            raise RuntimeError("Missing SHEETS_WORKSHEET_NAME")
        if not self._h_keyword or not self._h_planned_date:
            raise RuntimeError("Missing SHEETS_HEADER_KEYWORD / SHEETS_HEADER_PLANNED_DATE")

        info = settings.google_service_account_json
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        self._svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def planned_for_today(self) -> List[PlannedRow]:
        values = self._read_all_values()
        if not values:
            return []

        header = [str(x or "").strip() for x in values[0]]
        idx_keyword = _find_col_index(header, self._h_keyword)
        idx_date = _find_col_index(header, self._h_planned_date)
        if idx_keyword is None or idx_date is None:
            raise ExternalApiError("SheetsSchemaError", "header columns not found")

        today = today_jst_ymd()

        rows: List[PlannedRow] = []
        for r in values[1:]:
            kw = _get_cell(r, idx_keyword).strip()
            pd = _get_cell(r, idx_date).strip()
            if not kw or not pd:
                continue
            if pd == today:
                rows.append(PlannedRow(keyword=kw, planned_date=pd))
        return rows

    def get_snapshot(self, keyword: str, planned_date: str) -> Dict[str, Any]:
        values = self._read_all_values()
        if not values:
            return {}

        header = [str(x or "").strip() for x in values[0]]
        idx_keyword = _find_col_index(header, self._h_keyword)
        idx_date = _find_col_index(header, self._h_planned_date)
        if idx_keyword is None or idx_date is None:
            return {}

        kw_t = (keyword or "").strip()
        pd_t = (planned_date or "").strip()

        for r in values[1:]:
            kw = _get_cell(r, idx_keyword).strip()
            pd = _get_cell(r, idx_date).strip()
            if kw == kw_t and pd == pd_t:
                snap: Dict[str, Any] = {}
                for i, h in enumerate(header):
                    if not h:
                        continue
                    snap[h] = _get_cell(r, i)
                return snap

        return {}

    def _read_all_values(self) -> List[List[Any]]:
        rng = f"{self._worksheet}!A1:Z"
        resp = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet_id, range=rng)
            .execute()
        )
        return resp.get("values") or []


def _find_col_index(header_row: List[str], target: str) -> Optional[int]:
    t = (target or "").strip()
    if not t:
        return None
    for i, h in enumerate(header_row):
        if h == t:
            return i
    tl = t.lower()
    for i, h in enumerate(header_row):
        if (h or "").lower() == tl:
            return i
    return None


def _get_cell(row: List[Any], idx: int) -> str:
    if idx < 0:
        return ""
    if idx >= len(row):
        return ""
    return str(row[idx] or "")
