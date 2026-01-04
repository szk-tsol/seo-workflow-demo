from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Phase(str, Enum):
    OUTLINE_GENERATING = "OUTLINE_GENERATING"
    OUTLINE_REVIEW = "OUTLINE_REVIEW"
    OUTLINE_WAITING_FEEDBACK = "OUTLINE_WAITING_FEEDBACK"
    OUTLINE_CONFIRMED = "OUTLINE_CONFIRMED"

    PAPER_SEARCHING = "PAPER_SEARCHING"
    PAPER_REVIEW = "PAPER_REVIEW"
    PAPER_WAITING_FEEDBACK = "PAPER_WAITING_FEEDBACK"
    PAPER_CONFIRMED = "PAPER_CONFIRMED"

    BODY_GENERATING = "BODY_GENERATING"
    BODY_REVIEW = "BODY_REVIEW"
    BODY_WAITING_FEEDBACK = "BODY_WAITING_FEEDBACK"

    FINAL_REVIEW = "FINAL_REVIEW"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    DISCARDED = "DISCARDED"

    ERROR = "ERROR"


class SlackAction(str, Enum):
    ARTICLE_START = "ARTICLE_START"

    OUTLINE_APPROVE = "OUTLINE_APPROVE"
    OUTLINE_REQUEST_REVISION = "OUTLINE_REQUEST_REVISION"

    PAPER_SELECT = "PAPER_SELECT"
    PAPER_REQUEST_REVISION = "PAPER_REQUEST_REVISION"

    BODY_APPROVE = "BODY_APPROVE"
    BODY_REQUEST_REVISION = "BODY_REQUEST_REVISION"

    FINAL_APPROVE = "FINAL_APPROVE"
    FINAL_DISCARD = "FINAL_DISCARD"

    ARTICLE_PUBLISH = "ARTICLE_PUBLISH"
    RETRY = "RETRY"


@dataclass
class ArticleState:
    # identifiers
    article_id: str
    keyword: str
    planned_date: str

    # read-only snapshot (baseline at "作成する" time)
    sheet_snapshot: Dict[str, Any] = field(default_factory=dict)

    # workflow
    phase: Phase = Phase.OUTLINE_GENERATING

    # Slack
    slack_channel_id: str = ""
    slack_last_message_ts: Optional[str] = None
    slack_revision_thread_ts: Optional[str] = None

    # Outline
    outline_text: Optional[str] = None
    outline_feedback_text: Optional[str] = None
    outline_revision_count: int = 0

    # PubMed
    pubmed_query: Optional[str] = None
    paper_candidates: List[Dict[str, Any]] = field(default_factory=list)
    selected_pmid: Optional[str] = None
    paper_feedback_text: Optional[str] = None
    paper_revision_count: int = 0

    # Body
    body_text: Optional[str] = None
    body_feedback_text: Optional[str] = None
    body_revision_count: int = 0

    # WordPress
    wp_post_id: Optional[int] = None
    wp_post_url: Optional[str] = None
    wp_title: Optional[str] = None
    wp_slug: Optional[str] = None
    wp_categories: List[str] = field(default_factory=list)
    wp_tags: List[str] = field(default_factory=list)

    # Error + retry
    error_prev_phase: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_user_message: Optional[str] = None
    error_occurred_at: Optional[str] = None
    retry_available_until: Optional[str] = None

    # timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    phase_updated_at: Optional[str] = None

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ArticleState":
        phase_raw = str(d.get("phase") or "").strip()
        phase = Phase(phase_raw) if phase_raw else Phase.ERROR

        return ArticleState(
            article_id=str(d.get("article_id") or ""),
            keyword=str(d.get("keyword") or ""),
            planned_date=str(d.get("planned_date") or ""),
            sheet_snapshot=dict(d.get("sheet_snapshot") or {}),

            phase=phase,

            slack_channel_id=str(d.get("slack_channel_id") or ""),
            slack_last_message_ts=_opt_str(d.get("slack_last_message_ts")),
            slack_revision_thread_ts=_opt_str(d.get("slack_revision_thread_ts")),

            outline_text=_opt_str(d.get("outline_text")),
            outline_feedback_text=_opt_str(d.get("outline_feedback_text")),
            outline_revision_count=int(d.get("outline_revision_count") or 0),

            pubmed_query=_opt_str(d.get("pubmed_query")),
            paper_candidates=list(d.get("paper_candidates") or []),
            selected_pmid=_opt_str(d.get("selected_pmid")),
            paper_feedback_text=_opt_str(d.get("paper_feedback_text")),
            paper_revision_count=int(d.get("paper_revision_count") or 0),

            body_text=_opt_str(d.get("body_text")),
            body_feedback_text=_opt_str(d.get("body_feedback_text")),
            body_revision_count=int(d.get("body_revision_count") or 0),

            wp_post_id=_opt_int(d.get("wp_post_id")),
            wp_post_url=_opt_str(d.get("wp_post_url")),
            wp_title=_opt_str(d.get("wp_title")),
            wp_slug=_opt_str(d.get("wp_slug")),
            wp_categories=list(d.get("wp_categories") or []),
            wp_tags=list(d.get("wp_tags") or []),

            error_prev_phase=_opt_str(d.get("error_prev_phase")),
            error_type=_opt_str(d.get("error_type")),
            error_message=_opt_str(d.get("error_message")),
            error_user_message=_opt_str(d.get("error_user_message")),
            error_occurred_at=_opt_str(d.get("error_occurred_at")),
            retry_available_until=_opt_str(d.get("retry_available_until")),

            created_at=_opt_str(d.get("created_at")),
            updated_at=_opt_str(d.get("updated_at")),
            phase_updated_at=_opt_str(d.get("phase_updated_at")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_id": self.article_id,
            "keyword": self.keyword,
            "planned_date": self.planned_date,
            "sheet_snapshot": self.sheet_snapshot,

            "phase": self.phase.value,

            "slack_channel_id": self.slack_channel_id,
            "slack_last_message_ts": self.slack_last_message_ts,
            "slack_revision_thread_ts": self.slack_revision_thread_ts,

            "outline_text": self.outline_text,
            "outline_feedback_text": self.outline_feedback_text,
            "outline_revision_count": int(self.outline_revision_count),

            "pubmed_query": self.pubmed_query,
            "paper_candidates": self.paper_candidates,
            "selected_pmid": self.selected_pmid,
            "paper_feedback_text": self.paper_feedback_text,
            "paper_revision_count": int(self.paper_revision_count),

            "body_text": self.body_text,
            "body_feedback_text": self.body_feedback_text,
            "body_revision_count": int(self.body_revision_count),

            "wp_post_id": self.wp_post_id,
            "wp_post_url": self.wp_post_url,
            "wp_title": self.wp_title,
            "wp_slug": self.wp_slug,
            "wp_categories": self.wp_categories,
            "wp_tags": self.wp_tags,

            "error_prev_phase": self.error_prev_phase,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_user_message": self.error_user_message,
            "error_occurred_at": self.error_occurred_at,
            "retry_available_until": self.retry_available_until,

            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phase_updated_at": self.phase_updated_at,
        }


def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None
