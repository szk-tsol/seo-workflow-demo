from __future__ import annotations


import json
from typing import Any, Dict, List, Optional, Tuple

import anyio

from app.config import Settings
from app.domain import ArticleState, Phase
from app.integrations.openai_client import OpenAIClient
from app.integrations.pubmed import PubMedClient
from app.integrations.wordpress import WordPressClient
from app.integrations.slack.client import SlackClient
from app.integrations.slack.ui import SlackUI
from app.storage.firestore import FirestoreRepo
from app.storage.sheets import SheetsClient, PlannedRow
from app.utils.errors import (
AppError,
ExternalApiError,
SlackApiError,
PubMedNoResultsError,
PubMedTooManyResultsError,
OpenAIError,
WordPressError,
)
from app.utils.time import (
now_jst_iso,
add_days_jst_iso,
is_expired,
today_jst_ymd,
normalize_ymd,
generate_article_id,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class Services:


    def __init__(self, settings: Settings):
        self.settings = settings

        self.repo = FirestoreRepo(settings)
        self.sheets = SheetsClient(settings)

        self.slack = SlackClient(settings)
        self.ui = SlackUI()

        self.openai = OpenAIClient(settings)
        self.pubmed = PubMedClient(settings)
        self.wp = WordPressClient(settings)

    # -------------------------
    # Cron/Jobs entrypoint
    # -------------------------
    async def notify_planned(self) -> Dict[str, Any]:
        """
        Pull today's planned keywords from Google Sheets.
        Notify Slack with buttons "作成する / スキップ".
        """
        rows = await anyio.to_thread.run_sync(self.sheets.planned_for_today)
        ymd = today_jst_ymd()
        planned: List[Dict[str, Any]] = []
        for r in rows:
            planned.append({"keyword": r.keyword, "planned_date": r.planned_date})

        # Basic de-dupe by (keyword, date)
        uniq: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for p in planned:
            k = (str(p.get("keyword") or "").strip(), str(p.get("planned_date") or "").strip())
            if k[0] and k[1]:
                uniq[k] = p
        planned = list(uniq.values())

        # Optional: do not exceed daily max
        if planned:
            count = await anyio.to_thread.run_sync(self.repo.count_articles_for_date, ymd)
            if count >= self.settings.daily_max_articles:
                await self._slack_post(
                    channel=self.settings.slack_channel_id,
                    text=f"本日分の記事作成は上限（{self.settings.daily_max_articles}件）に達しています。通知をスキップします。",
                    blocks=None,
                )
                return {"ok": True, "count": 0, "planned": []}

        for p in planned:
            # notify_planned() 内
            blocks = self.ui.notify_planned_blocks(
                keyword=p["keyword"],
                planned_date=p["planned_date"]
            )
            await self._slack_post(
                channel=self.settings.slack_channel_id,
                text=f"本日の記事予定: {p['keyword']} ({p['planned_date']})",
                blocks=blocks,
            )
        return {"ok": True, "count": len(planned), "planned": planned}

    # -------------------------
    # Slack actions/events entrypoints
    # -------------------------
    async def process_slack_action(self, action: Dict[str, Any]) -> None:
        """
        Called from /slack/actions handler.
        action is normalized:
        { action_id, value, channel_id, message_ts }
        """
        action_id = (action.get("action_id") or "").strip()
        value = (action.get("value") or "").strip()
        channel_id = (action.get("channel_id") or "").strip()
        message_ts = (action.get("message_ts") or "").strip()

        if action_id == "create_article":
            keyword = value
            planned_date = today_jst_ymd()
            await self.start_article(keyword=keyword, planned_date=planned_date, slack_channel_id=channel_id)
            return

        if action_id == "skip_article":
            await self._slack_post(channel=channel_id, text=f"スキップしました: {value}", blocks=None)
            return

        # Below: per-article actions (value contains article_id)
        article_id = value
        if not article_id:
            return

        if action_id == "approve_outline":
            await self.approve_outline(article_id=article_id)
            return

        if action_id == "revise_outline":
            # message_ts is parent
            await self.request_outline_revision(article_id=article_id, parent_ts=message_ts)
            return

        if action_id == "approve_body":
            await self.approve_body(article_id=article_id)
            return

        if action_id == "revise_body":
            await self.request_body_revision(article_id=article_id, parent_ts=message_ts)
            return

        if action_id.startswith("select_paper_"):
            pmid = action_id.replace("select_paper_", "").strip()
            await self.select_paper(article_id=article_id, pmid=pmid)
            return

        if action_id == "revise_paper":
            await self.request_paper_revision(article_id=article_id, parent_ts=message_ts)
            return

        if action_id == "final_approve":
            await self.final_approve(article_id=article_id)
            return

        if action_id == "final_discard":
            await self.final_discard(article_id=article_id)
            return

        if action_id == "confirm_publish":
            await self.confirm_publish(article_id=article_id)
            return

        if action_id == "retry":
            await self.retry(article_id=article_id)
            return

    async def process_slack_thread_message(self, *, thread_ts: str, text: str) -> None:
        """
        Called when Slack message event is a thread reply.
        We match thread_ts with slack_revision_thread_ts stored in state.
        """
        thread_ts = (thread_ts or "").strip()
        if not thread_ts:
            return

        state = await anyio.to_thread.run_sync(self.repo.find_by_revision_thread_ts, thread_ts)
        if state is None:
            return

        feedback = (text or "").strip()
        if not feedback:
            return

        if state.phase == Phase.OUTLINE_WAITING_FEEDBACK:
            await self.receive_outline_feedback(article_id=state.article_id, feedback=feedback)
            return

        if state.phase == Phase.PAPER_WAITING_FEEDBACK:
            await self.receive_paper_feedback(article_id=state.article_id, feedback=feedback)
            return

        if state.phase == Phase.BODY_WAITING_FEEDBACK:
            await self.receive_body_feedback(article_id=state.article_id, feedback=feedback)
            return

    # -------------------------
    # Workflow steps
    # -------------------------
    async def start_article(self, *, keyword: str, planned_date: str, slack_channel_id: str) -> None:
        """
        Create initial ArticleState.
        Then trigger outline generation in background.
        """
        keyword = (keyword or "").strip()
        planned_date = normalize_ymd(planned_date)
        if not keyword or not planned_date:
            return

        # Snapshot in sheet is optional; can store in state
        snapshot: Optional[Dict[str, Any]] = None
        try:
            snapshot = await anyio.to_thread.run_sync(self.sheets.get_snapshot, keyword, planned_date)
        except Exception:
            snapshot = None

        article_id = generate_article_id(keyword=keyword, planned_date=planned_date)

        state = ArticleState(
            article_id=article_id,
            keyword=keyword,
            planned_date=planned_date,
            slack_channel_id=slack_channel_id,
            phase=Phase.OUTLINE_GENERATING,
            created_at=now_jst_iso(),
            phase_updated_at=now_jst_iso(),
            sheet_snapshot=snapshot,
        )

        await anyio.to_thread.run_sync(self.repo.create_article, state)

        await self._slack_post(
            channel=slack_channel_id,
            text=f"記事作成を開始しました。article_id={article_id}",
            blocks=None,
        )

        import asyncio
        asyncio.create_task(self.generate_outline(article_id=article_id))

    async def generate_outline(self, *, article_id: str) -> None:
        prev_phase = Phase.OUTLINE_GENERATING
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            outline = await anyio.to_thread.run_sync(
                self.openai.generate_outline,
                state.keyword,
                state.outline_text,
                state.outline_feedback_text,
                state.outline_revision_count,
            )

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "outline_text": outline,
                        "outline_feedback_text": None,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.OUTLINE_REVIEW,
                )
            )


            blocks = self.ui.outline_review_blocks(article_id=state.article_id, keyword=state.keyword, outline_text=outline)
            await self._slack_post(
                channel=state.slack_channel_id,
                text="構成案を作成しました。承認または修正指示をお願いします。",
                blocks=blocks,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def approve_outline(self, *, article_id: str) -> None:
        prev_phase = Phase.OUTLINE_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": None},
                    set_phase=Phase.OUTLINE_CONFIRMED,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="構成案を承認しました。論文候補を取得します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.search_papers(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def request_outline_revision(self, *, article_id: str, parent_ts: str) -> None:
        prev_phase = Phase.OUTLINE_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            if state.outline_revision_count >= 3:
                # go final review (approve/discard)
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"slack_revision_thread_ts": None},
                        set_phase=Phase.FINAL_REVIEW,
                    )
                )
                blocks = self.ui.final_review_blocks(article_id=article_id)
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="修正回数が上限に達しました。破棄または承認を選択してください。",
                    blocks=blocks,
                )
                return

            # store thread_ts and ask user to reply in thread
            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": parent_ts},
                    set_phase=Phase.OUTLINE_WAITING_FEEDBACK,
                )
            )

            blocks = self.ui.request_revision_instruction_blocks(target="outline")
            await self._slack_post(
                channel=state.slack_channel_id,
                text="構成案の修正指示をこのスレッドに返信してください。",
                blocks=blocks,
                thread_ts=parent_ts,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def receive_outline_feedback(self, *, article_id: str, feedback: str) -> None:
        prev_phase = Phase.OUTLINE_WAITING_FEEDBACK
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            # increment count (max 3 allowed)
            next_count = int(state.outline_revision_count) + 1

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "outline_feedback_text": feedback,
                        "outline_revision_count": next_count,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.OUTLINE_GENERATING,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="修正指示を受け取りました。構成案を再生成します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.generate_outline(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def search_papers(self, *, article_id: str) -> None:
        prev_phase = Phase.PAPER_SEARCHING
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            query = await anyio.to_thread.run_sync(
                self.openai.generate_pubmed_query,
                state.keyword,
                state.outline_text or "",
                state.paper_feedback_text,
                state.paper_revision_count,
            )

            papers = await anyio.to_thread.run_sync(
                lambda: self.pubmed.fetch_top_abstracts(query=query, retmax=3)
            )
            candidates = [p.to_dict() for p in papers]

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "pubmed_query": query,
                        "paper_candidates": candidates,
                        "paper_feedback_text": None,
                        "selected_pmid": None,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.PAPER_REVIEW,
                )
            )

            blocks = self.ui.paper_review_blocks(article_id=article_id, keyword=state.keyword, candidates=candidates)
            await self._slack_post(
                channel=state.slack_channel_id,
                text="論文候補を取得しました。選択または修正指示をお願いします。",
                blocks=blocks,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def request_paper_revision(self, *, article_id: str, parent_ts: str) -> None:
        prev_phase = Phase.PAPER_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            if state.paper_revision_count >= 3:
                # no final_review for papers; force user to select
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="論文検索の修正は上限に達しました。候補から選択してください。",
                    blocks=None,
                )
                return

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": parent_ts},
                    set_phase=Phase.PAPER_WAITING_FEEDBACK,
                )
            )

            blocks = self.ui.request_revision_instruction_blocks(target="paper")
            await self._slack_post(
                channel=state.slack_channel_id,
                text="論文検索の修正指示をこのスレッドに返信してください。",
                blocks=blocks,
                thread_ts=parent_ts,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def receive_paper_feedback(self, *, article_id: str, feedback: str) -> None:
        prev_phase = Phase.PAPER_WAITING_FEEDBACK
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            next_count = int(state.paper_revision_count) + 1

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "paper_feedback_text": feedback,
                        "paper_revision_count": next_count,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.PAPER_SEARCHING,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="修正指示を受け取りました。論文候補を再取得します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.search_papers(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def select_paper(self, *, article_id: str, pmid: str) -> None:
        prev_phase = Phase.PAPER_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            candidates = state.paper_candidates or []
            selected = self._find_selected_candidate(candidates, pmid)
            if selected is None:
                raise ExternalApiError("PaperNotFound", f"pmid not found in candidates: {pmid}")

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "selected_pmid": pmid,
                        "selected_paper": selected,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.BODY_GENERATING,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text=f"論文を選択しました（PMID={pmid}）。本文を生成します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.generate_body(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def generate_body(self, *, article_id: str) -> None:
        prev_phase = Phase.BODY_GENERATING
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            selected = state.selected_paper
            if not selected:
                # best-effort: find from candidates by pmid
                selected = self._find_selected_candidate(state.paper_candidates or [], state.selected_pmid)
            if not selected:
                raise ExternalApiError("NoSelectedPaper", "selected paper missing")

            body = await anyio.to_thread.run_sync(
                self.openai.generate_body,
                state.keyword,
                state.outline_text or "",
                selected,
                state.body_feedback_text,
                state.body_revision_count,
            )

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "body_text": body,
                        "body_feedback_text": None,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.BODY_REVIEW,
                )
            )

            blocks = self.ui.body_review_blocks(article_id=article_id, keyword=state.keyword, body_text=body)
            await self._slack_post(
                channel=state.slack_channel_id,
                text="本文を作成しました。承認または修正指示をお願いします。",
                blocks=blocks,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def approve_body(self, *, article_id: str) -> None:
        prev_phase = Phase.BODY_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": None},
                    set_phase=Phase.READY_TO_PUBLISH,
                )
            )

            blocks = self.ui.publish_confirm_blocks(article_id=article_id)
            await self._slack_post(
                channel=state.slack_channel_id,
                text="本文を承認しました。投稿しますか？",
                blocks=blocks,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def request_body_revision(self, *, article_id: str, parent_ts: str) -> None:
        prev_phase = Phase.BODY_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            if state.body_revision_count >= 3:
                # go final review
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"slack_revision_thread_ts": None},
                        set_phase=Phase.FINAL_REVIEW,
                    )
                )
                blocks = self.ui.final_review_blocks(article_id=article_id)
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="修正回数が上限に達しました。破棄または承認を選択してください。",
                    blocks=blocks,
                )
                return

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": parent_ts},
                    set_phase=Phase.BODY_WAITING_FEEDBACK,
                )
            )

            blocks = self.ui.request_revision_instruction_blocks(target="body")
            await self._slack_post(
                channel=state.slack_channel_id,
                text="本文の修正指示をこのスレッドに返信してください。",
                blocks=blocks,
                thread_ts=parent_ts,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def receive_body_feedback(self, *, article_id: str, feedback: str) -> None:
        prev_phase = Phase.BODY_WAITING_FEEDBACK
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            next_count = int(state.body_revision_count) + 1

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "body_feedback_text": feedback,
                        "body_revision_count": next_count,
                        "slack_revision_thread_ts": None,
                    },
                    set_phase=Phase.BODY_GENERATING,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="修正指示を受け取りました。本文を再生成します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.generate_body(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def final_approve(self, *, article_id: str) -> None:
        """
        Final approve at FINAL_REVIEW:
        - If outline exists and body exists -> move READY_TO_PUBLISH
        - Else decide best next step based on available fields
        """
        prev_phase = Phase.FINAL_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            if state.body_text:
                # go publish
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"slack_revision_thread_ts": None},
                        set_phase=Phase.READY_TO_PUBLISH,
                    )
                )
                blocks = self.ui.publish_confirm_blocks(article_id=article_id)
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="最終承認しました。投稿しますか？",
                    blocks=blocks,
                )
                return

            if state.selected_pmid:
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"slack_revision_thread_ts": None},
                        set_phase=Phase.BODY_GENERATING,
                    )
                )
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="最終承認しました。本文を生成します。",
                    blocks=None,
                )
                import asyncio
                asyncio.create_task(self.generate_body(article_id=article_id))
                return

            if state.outline_text:
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"slack_revision_thread_ts": None},
                        set_phase=Phase.PAPER_SEARCHING,
                    )
                )
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="最終承認しました。論文候補を取得します。",
                    blocks=None,
                )
                import asyncio
                asyncio.create_task(self.search_papers(article_id=article_id))
                return

            # fallback: outline generation
            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": None},
                    set_phase=Phase.OUTLINE_GENERATING,
                )
            )
            await self._slack_post(
                channel=state.slack_channel_id,
                text="最終承認しました。構成案を生成します。",
                blocks=None,
            )
            import asyncio
            asyncio.create_task(self.generate_outline(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def final_discard(self, *, article_id: str) -> None:
        prev_phase = Phase.FINAL_REVIEW
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={"slack_revision_thread_ts": None},
                    set_phase=Phase.DISCARDED,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="破棄しました。",
                blocks=None,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def confirm_publish(self, *, article_id: str) -> None:
        prev_phase = Phase.READY_TO_PUBLISH
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={},
                    set_phase=Phase.PUBLISHING,
                )
            )

            await self._slack_post(
                channel=state.slack_channel_id,
                text="投稿処理を開始します。",
                blocks=None,
            )

            import asyncio
            asyncio.create_task(self.publish_article(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def publish_article(self, *, article_id: str) -> None:
        prev_phase = Phase.PUBLISHING
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            # already published? detect by marker-search approach
            existing = await anyio.to_thread.run_sync(
                lambda: self.wp.find_existing_by_article_id(article_id=article_id)
            )
            if existing:
                post_id = int(existing.get("id") or 0)
                link = str(existing.get("link") or "")
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates={"wp_post_id": post_id, "wp_post_url": link},
                        set_phase=Phase.PUBLISHED,
                    )
                )
                blocks = self.ui.published_blocks(article_id=article_id, url=link)
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="投稿が完了しました。",
                    blocks=blocks,
                )
                return

            selected = self._find_selected_candidate(state.paper_candidates, state.selected_pmid)
            if selected is None:
                raise ExternalApiError("NoSelectedPaper", "selected paper not found")

            title, slug = await anyio.to_thread.run_sync(
                self.openai.generate_title_and_slug,
                state.keyword,
                state.outline_text or "",
                selected,
                state.body_text or "",
            )

            categories, tags = await anyio.to_thread.run_sync(
                self.openai.generate_categories_and_tags,
                state.keyword,
                state.outline_text or "",
                state.body_text or "",
            )

            cat_ids, tag_ids = await anyio.to_thread.run_sync(
                lambda: self.wp.ensure_terms(
                    categories=categories,
                    tags=tags,
                )
            )

            post_id, url = await anyio.to_thread.run_sync(
                lambda: self.wp.publish_post(
                    title=title,
                    slug=slug,
                    content=state.body_text or "",
                    category_ids=cat_ids,
                    tag_ids=tag_ids,
                    article_id=article_id,
                )
            )

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={
                        "wp_post_id": post_id,
                        "wp_post_url": url,
                        "wp_title": title,
                        "wp_slug": slug,
                        "wp_categories": categories,
                        "wp_tags": tags,
                    },
                    set_phase=Phase.PUBLISHED,
                )
            )

            blocks = self.ui.published_blocks(article_id=article_id, url=url)
            await self._slack_post(
                channel=state.slack_channel_id,
                text="投稿が完了しました。",
                blocks=blocks,
            )

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def retry(self, *, article_id: str) -> None:
        prev_phase = Phase.ERROR
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)

            # expiry check
            if not state.retry_available_until or is_expired(state.retry_available_until):
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="期限切れです。",
                    blocks=None,
                )
                return

            target_phase_raw = (state.error_prev_phase or "").strip()
            if not target_phase_raw:
                await self._slack_post(
                    channel=state.slack_channel_id,
                    text="再試行できません。",
                    blocks=None,
                )
                return

            # clear error fields and move phase back (phase_updated_at only when changed)
            await anyio.to_thread.run_sync(self.repo.clear_error, article_id)

            # set to target phase first
            try:
                target_phase = Phase(target_phase_raw)
            except Exception:
                target_phase = Phase.OUTLINE_GENERATING

            state = await anyio.to_thread.run_sync(
                lambda: self.repo.update_article_fields(
                    article_id,
                    updates={},
                    set_phase=target_phase,
                )
            )

            # trigger corresponding work (revision_count unchanged)
            import asyncio
            if target_phase in (Phase.OUTLINE_GENERATING,):
                asyncio.create_task(self.generate_outline(article_id=article_id))
            elif target_phase in (Phase.PAPER_SEARCHING,):
                asyncio.create_task(self.search_papers(article_id=article_id))
            elif target_phase in (Phase.BODY_GENERATING,):
                asyncio.create_task(self.generate_body(article_id=article_id))
            elif target_phase in (Phase.PUBLISHING,):
                asyncio.create_task(self.publish_article(article_id=article_id))
            else:
                # fallback: try to resume by phase
                if state.body_text:
                    asyncio.create_task(self.publish_article(article_id=article_id))
                elif state.selected_pmid:
                    asyncio.create_task(self.generate_body(article_id=article_id))
                elif state.outline_text:
                    asyncio.create_task(self.search_papers(article_id=article_id))
                else:
                    asyncio.create_task(self.generate_outline(article_id=article_id))

        except Exception as e:
            await self._handle_error(article_id=article_id, prev_phase=prev_phase, err=e)

    async def _handle_error(self, *, article_id: str, prev_phase: Phase, err: Exception) -> None:
        try:
            state = await anyio.to_thread.run_sync(self.repo.get_article, article_id)
        except Exception:
            state = None

        error_type, error_message, user_message = self._to_error_fields(err)

        patch = {
            "error_prev_phase": prev_phase.value if isinstance(prev_phase, Phase) else str(prev_phase),
            "error_type": error_type,
            "error_message": error_message,
            "error_user_message": user_message,
            "error_occurred_at": now_jst_iso(),
            "retry_available_until": add_days_jst_iso(7),
            "slack_revision_thread_ts": None,
        }

        if state is not None:
            try:
                state = await anyio.to_thread.run_sync(
                    lambda: self.repo.update_article_fields(
                        article_id,
                        updates=patch,
                        set_phase=Phase.ERROR,
                    )
                )
            except Exception:
                pass

        # user-facing: one-liner + retry button
        try:
            channel = state.slack_channel_id if state else self.settings.slack_channel_id
            blocks = self.ui.error_message_blocks(article_id=article_id)
            await self._slack_post(channel=channel, text=user_message, blocks=blocks)
        except Exception:
            pass

        logger.exception("workflow error", extra={"article_id": article_id, "error_type": error_type})

    def _to_error_fields(self, err: Exception) -> Tuple[str, str, str]:
        # user-facing fixed in v1
        user_message = "エラーが発生しました。"

        if isinstance(err, PubMedTooManyResultsError):
            return "PubMedTooManyResults", str(err), user_message
        if isinstance(err, PubMedNoResultsError):
            return "PubMedNoResults", str(err), user_message
        if isinstance(err, SlackApiError):
            return "SlackApiError", str(err), user_message
        if isinstance(err, OpenAIError):
            return "OpenAIError", str(err), user_message
        if isinstance(err, WordPressError):
            return "WordPressError", str(err), user_message
        if isinstance(err, ExternalApiError):
            return err.code or "ExternalApiError", str(err), user_message
        if isinstance(err, AppError):
            return err.code or "AppError", str(err), user_message

        return "UnknownError", str(err), user_message

    async def _slack_post(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]],
        thread_ts: Optional[str] = None,
    ) -> None:
        def _post():
            kwargs = {
                "channel": channel,
                "text": text,
            }
            if blocks is not None:
                kwargs["blocks"] = blocks
            if thread_ts is not None:
                kwargs["thread_ts"] = thread_ts

            return self.slack.post_message(**kwargs)

        await anyio.to_thread.run_sync(_post)

    def _find_selected_candidate(self, candidates: List[Dict[str, Any]], pmid: Optional[str]) -> Optional[Dict[str, Any]]:
        if not pmid:
            return None
        for c in candidates or []:
            if str(c.get("pmid") or "").strip() == pmid:
                return c
        return None
