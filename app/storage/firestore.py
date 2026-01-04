from __future__ import annotations

from typing import Any, Dict, Optional

from google.cloud import firestore
from google.oauth2 import service_account

from app.config import Settings
from app.domain import ArticleState, Phase
from app.utils.time import now_jst_iso


class FirestoreRepo:
    def __init__(self, settings: Settings):
        info = settings.google_service_account_json
        project_id = str(info.get("project_id") or "").strip()
        if not project_id:
            raise RuntimeError("service account JSON missing project_id")

        creds = service_account.Credentials.from_service_account_info(info)
        self._db = firestore.Client(project=project_id, credentials=creds)
        self._col = self._db.collection("articles")

    def _doc(self, article_id: str):
        return self._col.document(article_id)

    def create_article(self, state: ArticleState) -> None:
        now = now_jst_iso()
        d = state.to_dict()
        d["created_at"] = now
        d["updated_at"] = now
        d["phase_updated_at"] = now
        self._doc(state.article_id).set(d, merge=False)

    def get_article(self, article_id: str) -> ArticleState:
        snap = self._doc(article_id).get()
        if not snap.exists:
            raise KeyError(f"article not found: {article_id}")
        data = snap.to_dict() or {}
        return ArticleState.from_dict(data)

    def update_article_fields(
        self,
        article_id: str,
        updates: Dict[str, Any],
        set_phase: Optional[Phase] = None,
        phase_update_only_when_changed: bool = True,
    ) -> ArticleState:
        ref = self._doc(article_id)
        snap = ref.get()
        if not snap.exists:
            raise KeyError(f"article not found: {article_id}")

        current = snap.to_dict() or {}
        now = now_jst_iso()

        patch: Dict[str, Any] = dict(updates or {})
        patch["updated_at"] = now

        if set_phase is not None:
            cur_phase = str(current.get("phase") or "")
            new_phase = set_phase.value
            if (not phase_update_only_when_changed) or (cur_phase != new_phase):
                patch["phase"] = new_phase
                patch["phase_updated_at"] = now

        ref.set(patch, merge=True)

        snap2 = ref.get()
        return ArticleState.from_dict(snap2.to_dict() or {})

    def clear_error(self, article_id: str) -> None:
        ref = self._doc(article_id)
        patch = {
            "error_prev_phase": firestore.DELETE_FIELD,
            "error_type": firestore.DELETE_FIELD,
            "error_message": firestore.DELETE_FIELD,
            "error_user_message": firestore.DELETE_FIELD,
            "error_occurred_at": firestore.DELETE_FIELD,
            "retry_available_until": firestore.DELETE_FIELD,
            "updated_at": now_jst_iso(),
        }
        ref.set(patch, merge=True)

    def find_by_revision_thread_ts(self, thread_ts: str) -> Optional[ArticleState]:
        thread_ts = (thread_ts or "").strip()
        if not thread_ts:
            return None

        q = self._col.where("slack_revision_thread_ts", "==", thread_ts).limit(1)
        docs = list(q.stream())
        if not docs:
            return None

        data = docs[0].to_dict() or {}
        return ArticleState.from_dict(data)

    def count_articles_for_date(self, planned_date: str) -> int:
        planned_date = (planned_date or "").strip()
        if not planned_date:
            return 0
        q = self._col.where("planned_date", "==", planned_date)
        return sum(1 for _ in q.stream())
