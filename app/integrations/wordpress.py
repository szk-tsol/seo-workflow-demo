from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth

from app.config import Settings
from app.utils.errors import WordPressError


class WordPressClient:
    def __init__(self, settings: Settings):
        self.base = (settings.wp_base_url or "").rstrip("/")
        self.user = settings.wp_username
        self.passwd = settings.wp_app_password
        self.post_type = settings.wp_post_type or "posts"

        if not self.base or not self.user or not self.passwd:
            raise RuntimeError("Missing WP_BASE_URL / WP_USERNAME / WP_APP_PASSWORD")

        self.auth = HTTPBasicAuth(self.user, self.passwd)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def find_existing_by_article_id(self, *, article_id: str) -> Optional[Dict[str, Any]]:
        article_id = (article_id or "").strip()
        if not article_id:
            return None

        # v1 pragmatic approach:
        # - publish_post embeds marker in content: <!-- SEO_WORKFLOW_ARTICLE_ID=... -->
        # - use REST 'search' and scan content.rendered
        url = self._url(f"/wp-json/wp/v2/{self.post_type}")
        params = {"search": f"SEO_WORKFLOW_ARTICLE_ID={article_id}", "per_page": 20}

        r = requests.get(url, params=params, auth=self.auth, timeout=30)
        if r.status_code not in (200, 201):
            raise WordPressError(f"find_existing HTTP {r.status_code}: {r.text}")

        items = r.json()
        if not isinstance(items, list):
            return None

        marker = f"SEO_WORKFLOW_ARTICLE_ID={article_id}"
        for it in items:
            try:
                rendered = (it.get("content") or {}).get("rendered") or ""
                if marker in rendered:
                    return it
            except Exception:
                continue

        return None

    def ensure_terms(self, *, categories: List[str], tags: List[str]) -> Tuple[List[int], List[int]]:
        cat_ids: List[int] = []
        tag_ids: List[int] = []
        for c in categories or []:
            tid = self._ensure_term(taxonomy="categories", name=c)
            if tid:
                cat_ids.append(tid)
        for t in tags or []:
            tid = self._ensure_term(taxonomy="tags", name=t)
            if tid:
                tag_ids.append(tid)
        return cat_ids, tag_ids

    def _ensure_term(self, *, taxonomy: str, name: str) -> Optional[int]:
        name = (name or "").strip()
        if not name:
            return None

        list_url = self._url(f"/wp-json/wp/v2/{taxonomy}")

        # search existing
        r = requests.get(list_url, params={"search": name, "per_page": 100}, auth=self.auth, timeout=30)
        if r.status_code != 200:
            raise WordPressError(f"term search HTTP {r.status_code}: {r.text}")

        items = r.json()
        if isinstance(items, list):
            for it in items:
                if str(it.get("name") or "").strip() == name:
                    try:
                        return int(it.get("id"))
                    except Exception:
                        return None

        # create
        r2 = requests.post(list_url, json={"name": name}, auth=self.auth, timeout=30)
        if r2.status_code not in (200, 201):
            raise WordPressError(f"term create HTTP {r2.status_code}: {r2.text}")

        obj = r2.json()
        try:
            return int(obj.get("id"))
        except Exception:
            return None

    def publish_post(
        self,
        *,
        title: str,
        slug: str,
        content: str,
        category_ids: List[int],
        tag_ids: List[int],
        article_id: str,
    ) -> Tuple[int, str]:
        url = self._url(f"/wp-json/wp/v2/{self.post_type}")

        marker = f"<!-- SEO_WORKFLOW_ARTICLE_ID={article_id} -->"
        content_with_marker = (content or "").strip() + "\n\n" + marker + "\n"

        payload: Dict[str, Any] = {
            "status": "publish",
            "title": title,
            "slug": slug,
            "content": content_with_marker,
        }
        if category_ids:
            payload["categories"] = category_ids
        if tag_ids:
            payload["tags"] = tag_ids

        r = requests.post(url, json=payload, auth=self.auth, timeout=60)

        # requirement: HTTP 201 is success
        if r.status_code != 201:
            raise WordPressError(f"publish HTTP {r.status_code}: {r.text}")

        obj = r.json()
        post_id = int(obj.get("id") or 0)
        link = str(obj.get("link") or "")

        if not post_id or not link:
            raise WordPressError("publish returned invalid response")

        return post_id, link
