from __future__ import annotations

from typing import Any, Dict

from app.services import Services
from app.utils.jsonutil import safe_json_loads
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_value_to_str(raw: str) -> str:
    """
    Slack action value は、UI実装によって
    1) 素の文字列（keyword / article_id）
    2) JSON文字列（{"keyword": "..."} や {"article_id": "..."}）
    のどちらもあり得る。

    Services 側は value を str 前提で扱うため、ここで必ず str に正規化する。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    # JSON っぽければ解釈して、よく使うキーから文字列を抽出
    try:
        obj = safe_json_loads(raw)
    except Exception:
        return raw

    if isinstance(obj, dict):
        for k in ("keyword", "article_id", "pmid", "value"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # dict だが欲しいキーが無い場合は raw をそのまま
        return raw

    if isinstance(obj, str) and obj.strip():
        return obj.strip()

    return raw


async def handle_slack_actions(payload: Dict[str, Any], services: Services) -> None:
    """
    Slack interactive payload -> normalize -> services.process_slack_action()
    action["value"] は必ず str にする（Services 側の期待に合わせる）
    """
    try:
        channel_id = str((payload.get("channel") or {}).get("id") or "").strip()
        message_ts = str((payload.get("message") or {}).get("ts") or "").strip()

        actions = payload.get("actions") or []
        if not actions:
            return

        a0 = actions[0]
        action_id = str(a0.get("action_id") or "").strip()
        if not action_id:
            return

        # button: a0.value
        # static_select: a0.selected_option.value
        if a0.get("type") == "static_select":
            sel = a0.get("selected_option") or {}
            raw = str(sel.get("value") or "")
        else:
            raw = str(a0.get("value") or "")

        value_str = _normalize_value_to_str(raw)

        action = {
            "action_id": action_id,
            "channel_id": channel_id,
            "message_ts": message_ts,
            "value": value_str,  # ★必ず str
        }

        logger.info(
            "handle_slack_actions normalized",
            extra={"action_id": action_id, "value": value_str, "channel_id": channel_id, "message_ts": message_ts},
        )

        await services.process_slack_action(action)

    except Exception:
        # “黙って失敗” をやめて、必ずログに残す
        logger.exception("handle_slack_actions failed")
        return


async def handle_slack_events(payload: Dict[str, Any], services: Services) -> None:
    """
    Slack events payload -> pick thread replies -> services.process_slack_thread_message()
    """
    try:
        event = payload.get("event") or {}
        etype = str(event.get("type") or "").strip()

        if etype != "message":
            return

        # ignore bot messages / subtype messages
        if event.get("bot_id") or event.get("subtype"):
            return

        text = str(event.get("text") or "").strip()
        thread_ts = str(event.get("thread_ts") or "").strip()
        if not thread_ts:
            return

        logger.info("handle_slack_events thread_message", extra={"thread_ts": thread_ts})

        await services.process_slack_thread_message(thread_ts=thread_ts, text=text)

    except Exception:
        logger.exception("handle_slack_events failed")
        return

