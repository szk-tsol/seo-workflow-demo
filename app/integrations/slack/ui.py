from __future__ import annotations

import json
from typing import Any, Dict, List

from app.domain import SlackAction
from app.utils.jsonutil import json_dumps_compact


class SlackUI:
    def notify_planned_blocks(self, *, keyword: str, planned_date: str) -> List[Dict[str, Any]]:
        value = json_dumps_compact({"keyword": keyword, "planned_date": planned_date})
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*本日作成予定*  {planned_date}\nキーワード: {keyword}"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.ARTICLE_START.value,
                        "text": {"type": "plain_text", "text": "作成する"},
                        "value": value,
                    }
                ],
            },
        ]

    def outline_review_blocks(self, *, article_id: str, keyword: str, outline_text: str) -> List[Dict[str, Any]]:
        v = json_dumps_compact({"article_id": article_id})
        # Outline is plain text (not markdown). Slack block text supports mrkdwn, but content is plain.
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*構成案*  article_id={article_id}\nキーワード: {keyword}"}},
            {"type": "section", "text": {"type": "plain_text", "text": outline_text[:2800]}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.OUTLINE_APPROVE.value,
                        "text": {"type": "plain_text", "text": "承認"},
                        "style": "primary",
                        "value": v,
                    },
                    {
                        "type": "button",
                        "action_id": SlackAction.OUTLINE_REQUEST_REVISION.value,
                        "text": {"type": "plain_text", "text": "修正指示"},
                        "value": v,
                    },
                ],
            },
        ]

    def request_revision_instruction_blocks(self, *, target: str) -> List[Dict[str, Any]]:
        # Informational; no actions.
        text = "修正指示をこのスレッドに返信してください。"
        if target == "paper":
            text = "論文検索の修正指示をこのスレッドに返信してください。"
        if target == "body":
            text = "本文の修正指示をこのスレッドに返信してください。"
        return [{"type": "section", "text": {"type": "plain_text", "text": text}}]

    def paper_review_blocks(self, *, article_id: str, keyword: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # static_select option.value contains {"article_id","pmid"}
        options = []
        for c in candidates[:3]:
            pmid = str(c.get("pmid") or "").strip()
            title = str(c.get("title") or "").strip()
            label = f"{pmid}  {title[:60]}".strip()
            options.append(
                {
                    "text": {"type": "plain_text", "text": label[:75]},
                    "value": json_dumps_compact({"article_id": article_id, "pmid": pmid}),
                }
            )

        v = json_dumps_compact({"article_id": article_id})

        blocks: List[Dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*論文候補*  article_id={article_id}\nキーワード: {keyword}"}},
        ]

        for c in candidates[:3]:
            pmid = str(c.get("pmid") or "").strip()
            title = str(c.get("title") or "").strip()
            abstract = str(c.get("abstract") or "").strip()
            url = str(c.get("url") or "").strip()
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"PMID: {pmid}\n{title}\n{abstract[:700]}\n{url}",
                    },
                }
            )

        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": SlackAction.PAPER_SELECT.value,
                        "placeholder": {"type": "plain_text", "text": "論文を選択"},
                        "options": options,
                    },
                    {
                        "type": "button",
                        "action_id": SlackAction.PAPER_REQUEST_REVISION.value,
                        "text": {"type": "plain_text", "text": "修正指示"},
                        "value": v,
                    },
                ],
            }
        )
        return blocks

    def body_review_blocks(self, *, article_id: str, keyword: str, body_text: str) -> List[Dict[str, Any]]:
        v = json_dumps_compact({"article_id": article_id})
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*本文*  article_id={article_id}\nキーワード: {keyword}"}},
            {"type": "section", "text": {"type": "plain_text", "text": body_text[:2800]}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.BODY_APPROVE.value,
                        "text": {"type": "plain_text", "text": "承認"},
                        "style": "primary",
                        "value": v,
                    },
                    {
                        "type": "button",
                        "action_id": SlackAction.BODY_REQUEST_REVISION.value,
                        "text": {"type": "plain_text", "text": "修正指示"},
                        "value": v,
                    },
                ],
            },
        ]

    def final_review_blocks(self, *, article_id: str) -> List[Dict[str, Any]]:
        v = json_dumps_compact({"article_id": article_id})
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*最終判断*  article_id={article_id}"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.FINAL_APPROVE.value,
                        "text": {"type": "plain_text", "text": "承認"},
                        "style": "primary",
                        "value": v,
                    },
                    {
                        "type": "button",
                        "action_id": SlackAction.FINAL_DISCARD.value,
                        "text": {"type": "plain_text", "text": "破棄"},
                        "style": "danger",
                        "value": v,
                    },
                ],
            },
        ]

    def ready_to_publish_blocks(self, *, article_id: str) -> List[Dict[str, Any]]:
        v = json_dumps_compact({"article_id": article_id})
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*投稿前 最終確認*  article_id={article_id}"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.ARTICLE_PUBLISH.value,
                        "text": {"type": "plain_text", "text": "投稿する"},
                        "style": "primary",
                        "value": v,
                    }
                ],
            },
        ]

    def published_blocks(self, *, article_id: str, url: str) -> List[Dict[str, Any]]:
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*投稿完了*  article_id={article_id}\n{url}"}},
        ]

    def discarded_blocks(self, *, article_id: str) -> List[Dict[str, Any]]:
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*破棄*  article_id={article_id}"}},
        ]

    def error_message_blocks(self, *, article_id: str) -> List[Dict[str, Any]]:
        v = json_dumps_compact({"article_id": article_id})
        return [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*エラー*  article_id={article_id}"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": SlackAction.RETRY.value,
                        "text": {"type": "plain_text", "text": "再試行"},
                        "value": v,
                    }
                ],
            },
        ]
