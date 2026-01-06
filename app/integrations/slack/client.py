from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from app.config import Settings
from app.utils.errors import SlackApiError


class SlackClient:
    def __init__(self, settings: Settings):
        if not settings.slack_bot_token:
            raise RuntimeError("Missing SLACK_BOT_TOKEN")
        self._token = settings.slack_bot_token

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        payload: Dict[str, Any] = {"channel": channel, "text": text or ""}
        if blocks is not None:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts

        # connect/read を分けておくとネットワーク詰まりの切り分けがしやすい
        resp = requests.post(url, headers=headers, json=payload, timeout=(5, 30))

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise SlackApiError(f"rate_limited retry_after={retry_after} body={resp.text}")

        if resp.status_code != 200:
            raise SlackApiError(f"HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except ValueError:
            raise SlackApiError(f"invalid_json_response: {resp.text}")

        if not data.get("ok"):
            raise SlackApiError(str(data.get("error") or "unknown_error"))

        return data
