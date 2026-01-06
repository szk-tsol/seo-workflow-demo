from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Settings:
    # Slack
    slack_bot_token: str
    slack_signing_secret: str
    slack_channel_id: str

    # Jobs token (protect /jobs/*)
    jobs_token: str

    # OpenAI
    openai_api_key: str
    openai_model: str

    # Google Sheets (read-only)
    sheets_spreadsheet_id: str
    sheets_worksheet_name: str
    sheets_header_keyword: str
    sheets_header_planned_date: str

    # Google service account JSON (Sheets + Firestore)
    google_service_account_json: Dict[str, Any]

    # PubMed (NCBI E-utilities)
    ncbi_tool: str
    ncbi_email: str
    ncbi_api_key: str

    # WordPress REST
    wp_base_url: str
    wp_username: str
    wp_app_password: str
    wp_post_type: str

    daily_max_articles: int

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings

    def env(name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()

    sa_json_raw = env("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_json_raw:
        raise RuntimeError("Missing env GOOGLE_SERVICE_ACCOUNT_JSON (service account JSON string)")

    try:
        sa_info = json.loads(sa_json_raw)
        if not isinstance(sa_info, dict):
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must be a JSON object")
    except Exception as e:
        raise RuntimeError(f"Invalid GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

    _settings = Settings(
        slack_bot_token=env("SLACK_BOT_TOKEN"),
        slack_signing_secret=env("SLACK_SIGNING_SECRET"),
        slack_channel_id=env("SLACK_CHANNEL_ID"),

        jobs_token=env("JOBS_TOKEN"),

        openai_api_key=env("OPENAI_API_KEY"),
        openai_model=env("OPENAI_MODEL", "gpt-4.1-mini"),

        sheets_spreadsheet_id=env("SHEETS_SPREADSHEET_ID"),
        sheets_worksheet_name=env("SHEETS_WORKSHEET_NAME", "Sheet1"),
        sheets_header_keyword=env("SHEETS_HEADER_KEYWORD", "keyword"),
        sheets_header_planned_date=env("SHEETS_HEADER_PLANNED_DATE", "planned_date"),

        google_service_account_json=sa_info,

        ncbi_tool=env("NCBI_TOOL", "seo-workflow"),
        ncbi_email=env("NCBI_EMAIL", "example@example.com"),
        ncbi_api_key=env("NCBI_API_KEY", ""),

        wp_base_url=env("WP_BASE_URL"),
        wp_username=env("WP_USERNAME"),
        wp_app_password=env("WP_APP_PASSWORD"),
        wp_post_type=env("WP_POST_TYPE", "posts"),

        daily_max_articles=int(env("DAILY_MAX_ARTICLES", "20")),
    )
    return _settings
