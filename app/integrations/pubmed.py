from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import xml.etree.ElementTree as ET

import requests

from app.config import Settings
from app.utils.errors import PubMedNoResultsError, PubMedTooManyResultsError, ExternalApiError


@dataclass
class PubMedPaper:
    pmid: str
    title: str
    abstract: str
    url: str

    def to_dict(self):
        return {
            "pmid": self.pmid,
            "title": self.title,
            "abstract": self.abstract,
            "url": self.url,
        }


class PubMedClient:
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, settings: Settings):
        self.tool = settings.ncbi_tool
        self.email = settings.ncbi_email
        self.api_key = settings.ncbi_api_key

    def fetch_top_abstracts(self, query: str, retmax: int = 3) -> List[PubMedPaper]:
        query = (query or "").strip()
        if not query:
            raise ExternalApiError("InvalidQuery", "PubMed query is empty")

        ids, count = self._esearch(query=query, retmax=retmax)

        if count is not None and count > 10000:
            raise PubMedTooManyResultsError("PubMed result count exceeded 10,000")

        if not ids:
            raise PubMedNoResultsError("PubMed returned no results")

        papers = self._efetch_abstracts(pmids=ids)
        return papers[:retmax]

    def _esearch(self, *, query: str, retmax: int) -> tuple[List[str], Optional[int]]:
        url = f"{self.BASE}/esearch.fcgi"
        params = {
            "db": "pubmed",
            "term": query,
            "retmode": "xml",
            "retmax": str(retmax),
            "tool": self.tool,
            "email": self.email,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            raise ExternalApiError("PubMedHttpError", f"HTTP {r.status_code}: {r.text}")

        root = ET.fromstring(r.text)

        count = None
        count_node = root.findtext("Count")
        if count_node:
            try:
                count = int(count_node)
            except Exception:
                count = None

        id_list = []
        for id_el in root.findall("./IdList/Id"):
            if id_el.text:
                id_list.append(id_el.text.strip())
        return id_list, count

    def _efetch_abstracts(self, *, pmids: List[str]) -> List[PubMedPaper]:
        url = f"{self.BASE}/efetch.fcgi"
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "tool": self.tool,
            "email": self.email,
        }
        if self.api_key:
            params["api_key"] = self.api_key

        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            raise ExternalApiError("PubMedHttpError", f"HTTP {r.status_code}: {r.text}")

        root = ET.fromstring(r.text)

        papers: List[PubMedPaper] = []
        for art in root.findall(".//PubmedArticle"):
            pmid = (art.findtext(".//MedlineCitation/PMID") or "").strip()
            title = (art.findtext(".//Article/ArticleTitle") or "").strip()

            abs_parts = []
            for a in art.findall(".//Article/Abstract/AbstractText"):
                if a.text:
                    abs_parts.append(a.text.strip())
            abstract = "\n".join(abs_parts).strip()

            if not pmid:
                continue

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            papers.append(PubMedPaper(pmid=pmid, title=title, abstract=abstract, url=url))

        if not papers:
            raise PubMedNoResultsError("PubMed efetch returned empty articles")

        return papers
