from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings
from app.utils.errors import OpenAIError
from app.utils.jsonutil import json_dumps_compact, safe_json_loads


class OpenAIClient:
    def __init__(self, settings: Settings):
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        if not self.api_key:
            raise RuntimeError("Missing OPENAI_API_KEY")

        # Lazy import to avoid import errors at build-time if deps mismatch
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:
            raise RuntimeError(f"openai package import failed: {e}")

        self._client = OpenAI(api_key=self.api_key)

    def _chat(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            raise OpenAIError(str(e))

    def generate_outline(
        self,
        keyword: str,
        prev_outline: Optional[str],
        feedback: Optional[str],
        revision_count: int,
    ) -> str:
        system = (
            "You are an assistant that drafts Japanese article outlines.\n"
            "Return plain text only. Do not use Markdown.\n"
            "Write a clear outline with numbered headings.\n"
        )

        user = f"キーワード: {keyword}\n"
        user += f"修正回数: {revision_count}\n"
        if prev_outline:
            user += f"\n前回の構成案:\n{prev_outline}\n"
        if feedback:
            user += f"\n修正指示:\n{feedback}\n"
        user += "\n要件:\n"
        user += "- 医療系の記事を想定\n"
        user += "- 見出しは過不足なく、読み手にとって自然な流れ\n"
        user += "- 文章は日本語\n"
        user += "- Markdownは禁止（記号を多用しない）\n"
        user += "\n構成案を作成してください。"

        out = self._chat(system=system, user=user, temperature=0.2)
        if not out:
            raise OpenAIError("empty outline")
        return out

    def generate_pubmed_query(
        self,
        keyword: str,
        outline_text: str,
        paper_feedback: Optional[str],
        paper_revision_count: int,
    ) -> str:
        system = (
            "You are an assistant that creates PubMed search queries.\n"
            "Return ONLY a PubMed query string (no extra text).\n"
            "Avoid extremely broad queries.\n"
        )

        user = f"キーワード: {keyword}\n"
        user += f"修正回数: {paper_revision_count}\n"
        user += f"\n記事構成:\n{outline_text}\n"
        if paper_feedback:
            user += f"\n修正指示:\n{paper_feedback}\n"
        user += "\n要件:\n"
        user += "- PubMed(term) に使えるクエリ文字列を1つ\n"
        user += "- 臨床系の関連論文が出るようにする\n"
        user += "- 結果が広すぎないようにする\n"
        user += "- 返答はクエリ文字列のみ\n"

        q = self._chat(system=system, user=user, temperature=0.2)
        q = q.strip().strip('"').strip()
        if not q:
            raise OpenAIError("empty pubmed query")
        return q

    def generate_body(
        self,
        keyword: str,
        outline_text: str,
        selected_paper: Dict[str, Any],
        prev_body: Optional[str],
        feedback: Optional[str],
        revision_count: int,
    ) -> str:
        system = (
            "You are an assistant that drafts Japanese medical articles.\n"
            "Return plain text only. Do not use Markdown.\n"
            "Use the outline as structure.\n"
            "Cite paper in a simple way like: (PMID: XXXXXXXX).\n"
        )

        pmid = str(selected_paper.get("pmid") or "").strip()
        title = str(selected_paper.get("title") or "").strip()
        abstract = str(selected_paper.get("abstract") or "").strip()

        user = f"キーワード: {keyword}\n"
        user += f"修正回数: {revision_count}\n"
        user += f"\n構成:\n{outline_text}\n"
        user += f"\n参照論文:\nPMID: {pmid}\nTitle: {title}\nAbstract:\n{abstract}\n"
        if prev_body:
            user += f"\n前回の本文:\n{prev_body}\n"
        if feedback:
            user += f"\n修正指示:\n{feedback}\n"
        user += "\n要件:\n"
        user += "- 構成に沿って本文を作成\n"
        user += "- 可能な範囲で論文の知見を反映\n"
        user += "- 誇張しない。論文に無いことは断定しない\n"
        user += "- Markdown禁止\n"
        user += "- 文章は自然な日本語\n"
        user += "\n本文を作成してください。"

        out = self._chat(system=system, user=user, temperature=0.2)
        if not out:
            raise OpenAIError("empty body")
        return out

    def generate_title_and_slug(
        self,
        keyword: str,
        outline_text: str,
        selected_paper: Dict[str, Any],
        body_text: str,
    ) -> Tuple[str, str]:
        system = (
            "You are an assistant that outputs JSON only.\n"
            "Return ONLY JSON with keys: title_ja, slug_en.\n"
            "slug_en must be lowercase, hyphen-separated, ascii.\n"
        )

        user = f"キーワード: {keyword}\n"
        user += f"\n構成:\n{outline_text}\n"
        user += f"\n本文:\n{body_text[:2000]}\n"
        user += "\n要件:\n"
        user += "- title_ja は日本語の自然なタイトル\n"
        user += "- slug_en は英語の短いslug\n"
        user += "- JSONのみで返す\n"

        raw = self._chat(system=system, user=user, temperature=0.2)
        obj = safe_json_loads(raw)
        title = str(obj.get("title_ja") or "").strip()
        slug = str(obj.get("slug_en") or "").strip().lower()
        if not title or not slug:
            raise OpenAIError("invalid title/slug json")
        return title, slug

    def generate_categories_and_tags(
        self,
        keyword: str,
        outline_text: str,
        body_text: str,
    ) -> Tuple[List[str], List[str]]:
        system = (
            "You are an assistant that outputs JSON only.\n"
            "Return ONLY JSON with keys: categories, tags.\n"
            "categories and tags are arrays of Japanese strings.\n"
            "Avoid too many items.\n"
        )

        user = f"キーワード: {keyword}\n"
        user += f"\n構成:\n{outline_text}\n"
        user += f"\n本文:\n{body_text[:1500]}\n"
        user += "\n要件:\n"
        user += "- categories: 1〜2個\n"
        user += "- tags: 3〜6個\n"
        user += "- すべて日本語\n"
        user += "- JSONのみで返す\n"

        raw = self._chat(system=system, user=user, temperature=0.2)
        obj = safe_json_loads(raw)
        cats = obj.get("categories") or []
        tags = obj.get("tags") or []
        if not isinstance(cats, list) or not isinstance(tags, list):
            raise OpenAIError("invalid categories/tags json")

        categories = [str(x).strip() for x in cats if str(x).strip()]
        tag_list = [str(x).strip() for x in tags if str(x).strip()]

        if not categories:
            categories = ["医療"]
        if not tag_list:
            tag_list = [keyword]

        return categories[:2], tag_list[:6]
