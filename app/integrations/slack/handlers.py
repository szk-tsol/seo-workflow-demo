from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.services import Services
from app.utils.jsonutil import safe_json_loads


async def handle_slack_actions(payload: Dict[str, Any], services: Services) -> None:
    """
    Slack interactive payload -> normalize -> services.process_slack_action()
    """
    try:
        channel_id = str((payload.get("channel") or {}).get("id") or "").strip()
        message_ts = str((payload.get("message") or {}).get("ts") or "").strip()

        actions = payload.get("actions") or []
        if not actions:
            return

        a0 = actions[0]
        action_id = str(a0.get("action_id") or "").strip()

        # button: a0.value is JSON string
        # static_select: a0.selected_option.value is JSON string
        value_obj: Dict[str, Any] = {}

        if a0.get("type") == "static_select":
            sel = a0.get("selected_option") or {}
            raw = str(sel.get("value") or "").strip()
            value_obj = safe_json_loads(raw)
        else:
            raw = str(a0.get("value") or "").strip()
            if raw:
                value_obj = safe_json_loads(raw)

        action = {
            "action_id": action_id,
            "channel_id": channel_id,
            "message_ts": message_ts,
            "value": value_obj,
        }
        await services.process_slack_action(action)
    except Exception:
        # swallow; errors are handled in services when possible
        return


async def handle_slack_events(payload: Dict[str, Any], services: Services) -> None:
    """
    Slack events payload -> pick thread replies -> services.process_slack_thread_message()
    """
    try:
        event = payload.get("event") or {}
        etype = str(event.get("type") or "").strip()

        # ignore non-message
        if etype != "message":
            return

        # ignore bot messages
        if event.get("bot_id") or event.get("subtype"):
            return

        text = str(event.get("text") or "").strip()
        thread_ts = str(event.get("thread_ts") or "").strip()

        if not thread_ts:
            return

        await services.process_slack_thread_message(thread_ts=thread_ts, text=text)
    except Exception:
        return
