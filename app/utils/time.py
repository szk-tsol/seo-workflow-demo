from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def now_jst_iso() -> str:
    return datetime.now(JST).isoformat()


def today_jst_ymd() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def normalize_ymd(s: str) -> str:
    s = (s or "").strip()
    # accept YYYY/MM/DD
    if "/" in s and len(s) >= 10:
        parts = s.split("/")
        if len(parts) >= 3:
            y = parts[0].zfill(4)
            m = parts[1].zfill(2)
            d = parts[2].zfill(2)
            return f"{y}-{m}-{d}"
    return s


def add_days_jst_iso(days: int) -> str:
    dt = datetime.now(JST) + timedelta(days=int(days))
    return dt.isoformat()


def is_expired(until_iso: str) -> bool:
    try:
        dt = datetime.fromisoformat(until_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return datetime.now(JST) > dt.astimezone(JST)
    except Exception:
        return True


def generate_article_id(*, planned_date: str, seq: int) -> str:
    ymd = normalize_ymd(planned_date).replace("-", "")
    n = max(1, int(seq))
    return f"ART-{ymd}-{n:03d}"
