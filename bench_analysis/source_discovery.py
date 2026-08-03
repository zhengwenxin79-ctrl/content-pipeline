from __future__ import annotations

import re
import ssl
import json
from datetime import date
from html import unescape
from urllib.parse import parse_qs, urlencode, unquote, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .schema import SourceRecord


USER_AGENT = "bench-analysis-mvp/0.2"
SEARCH_URL = "https://duckduckgo.com/html/"
SSL_CONTEXT = ssl._create_unverified_context()
SOURCE_TYPE_PRIORITY = {
    "official": 1.0,
    "project": 0.9,
    "paper": 0.85,
    "leaderboard": 0.8,
    "github": 0.72,
    "dataset": 0.68,
}


def source_type_for_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if "arxiv.org" in host:
        return "paper"
    if "github.com" in host:
        return "github"
    if "huggingface.co" in host:
        return "dataset"
    if "paperswithcode.com" in host:
        return "leaderboard"
    if "leaderboard" in path or "benchmark" in path and "vals.ai" in host:
        return "leaderboard"
    if path.endswith(".pdf"):
        return "paper"
    if "openreview.net" in host or "aclanthology.org" in host:
        return "paper"
    return "project"


def build_queries(bench_name: str, aliases: list[str] | None = None) -> list[str]:
    names = [bench_name, *(aliases or [])]
    query_templates = [
        '"{name}" benchmark official',
        '"{name}" arxiv',
        '"{name}" GitHub',
        '"{name}" leaderboard',
        '"{name}" dataset',
        '"{name}" model scores',
    ]
    queries = []
    for name in names[:3]:
        queries.extend(template.format(name=name) for template in query_templates)
    return queries


def _clean_duckduckgo_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.netloc.endswith("duckduckgo.com"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return value


def _score_result(bench_name: str, title: str, url: str, snippet: str) -> float:
    haystack = f"{title} {url} {snippet}".lower()
    compact_name = re.sub(r"[^a-z0-9]+", "", bench_name.lower())
    compact_haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    score = 0.2
    if compact_name and compact_name in compact_haystack:
        score += 0.35
    if "benchmark" in haystack or "bench" in haystack:
        score += 0.15
    if any(token in haystack for token in ["arxiv", "github", "leaderboard", "dataset", "official"]):
        score += 0.15
    if source_type_for_url(url) in {"paper", "github", "dataset", "leaderboard"}:
        score += 0.1
    return min(score, 1.0)


def _open_text(url: str, timeout: int = 5) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return response.read().decode("utf-8", errors="ignore")


def search_web(query: str, limit: int = 5, timeout: int = 12) -> list[SourceRecord]:
    payload = urlencode({"q": query}).encode("utf-8")
    request = Request(
        SEARCH_URL,
        data=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        html = response.read().decode("utf-8", errors="ignore")
    records = []
    blocks = re.split(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>', html)
    for block in blocks[: limit * 3]:
        link_match = re.search(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not link_match:
            continue
        url = _clean_duckduckgo_url(unescape(link_match.group("href")))
        if not url.startswith(("http://", "https://")):
            continue
        snippet_match = re.search(
            r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>|'
            r'<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet2>.*?)</div>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet_html = snippet_match.group("snippet") or snippet_match.group("snippet2") if snippet_match else ""
        title = re.sub(r"<[^>]+>", " ", link_match.group("title"))
        snippet = re.sub(r"<[^>]+>", " ", snippet_html)
        title = re.sub(r"\s+", " ", unescape(title)).strip()
        snippet = re.sub(r"\s+", " ", unescape(snippet)).strip()
        records.append(
            SourceRecord(
                title=title,
                url=url,
                type=source_type_for_url(url),
                note=snippet[:240],
                discovered_by=f"duckduckgo:{query}",
                retrieved_date=date.today().isoformat(),
            )
        )
        if len(records) >= limit:
            break
    return records


def search_arxiv(bench_name: str, limit: int = 3) -> list[SourceRecord]:
    url = "https://export.arxiv.org/api/query?" + urlencode(
        {"search_query": f'all:"{bench_name}"', "start": 0, "max_results": limit}
    )
    try:
        xml_text = _open_text(url)
    except OSError:
        return []
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    records = []
    for entry in root.findall("atom:entry", namespace):
        title = entry.findtext("atom:title", default="", namespaces=namespace)
        summary = entry.findtext("atom:summary", default="", namespaces=namespace)
        link = ""
        for link_node in entry.findall("atom:link", namespace):
            href = link_node.attrib.get("href", "")
            if "/abs/" in href:
                link = href
                break
        if not link:
            continue
        records.append(
            SourceRecord(
                title=re.sub(r"\s+", " ", title).strip(),
                url=link,
                type="paper",
                note=re.sub(r"\s+", " ", summary).strip()[:240],
                relevance_score=_score_result(bench_name, title, link, summary),
                discovered_by="arxiv-api",
                retrieved_date=date.today().isoformat(),
            )
        )
    return records


def search_github(bench_name: str, limit: int = 3) -> list[SourceRecord]:
    url = "https://api.github.com/search/repositories?" + urlencode(
        {"q": f'"{bench_name}" benchmark', "sort": "stars", "order": "desc", "per_page": limit}
    )
    try:
        payload = json.loads(_open_text(url))
    except (OSError, json.JSONDecodeError):
        return []
    records = []
    for item in payload.get("items", [])[:limit]:
        repo_url = item.get("html_url", "")
        if not repo_url:
            continue
        title = item.get("full_name", repo_url)
        note = item.get("description") or ""
        records.append(
            SourceRecord(
                title=title,
                url=repo_url,
                type="github",
                note=note[:240],
                relevance_score=_score_result(bench_name, title, repo_url, note),
                discovered_by="github-api",
                retrieved_date=date.today().isoformat(),
            )
        )
    return records


def search_huggingface(bench_name: str, limit: int = 3) -> list[SourceRecord]:
    url = "https://huggingface.co/api/datasets?" + urlencode({"search": bench_name, "limit": limit})
    try:
        payload = json.loads(_open_text(url))
    except (OSError, json.JSONDecodeError):
        return []
    records = []
    for item in payload[:limit]:
        dataset_id = item.get("id") or item.get("name", "")
        if not dataset_id:
            continue
        dataset_url = f"https://huggingface.co/datasets/{dataset_id}"
        records.append(
            SourceRecord(
                title=dataset_id,
                url=dataset_url,
                type="dataset",
                note="Hugging Face dataset search result.",
                relevance_score=_score_result(bench_name, dataset_id, dataset_url, ""),
                discovered_by="huggingface-api",
                retrieved_date=date.today().isoformat(),
            )
        )
    return records


def discover_sources(
    bench_name: str,
    aliases: list[str] | None = None,
    seed_sources: list[SourceRecord] | None = None,
    limit: int = 10,
    include_general_search: bool = False,
) -> list[SourceRecord]:
    by_url: dict[str, SourceRecord] = {}
    for source in seed_sources or []:
        source.relevance_score = max(source.relevance_score, 0.95)
        source.retrieved_date = source.retrieved_date or date.today().isoformat()
        by_url[source.url] = source

    if include_general_search:
        for query in build_queries(bench_name, aliases):
            try:
                results = search_web(query, limit=4, timeout=5)
            except OSError:
                continue
            for result in results:
                result.relevance_score = _score_result(bench_name, result.title, result.url, result.note)
                existing = by_url.get(result.url)
                if existing is None or result.relevance_score > existing.relevance_score:
                    by_url[result.url] = result
            if len(by_url) >= limit * 2:
                break

    for result in [*search_arxiv(bench_name), *search_github(bench_name), *search_huggingface(bench_name)]:
        existing = by_url.get(result.url)
        if existing is None or result.relevance_score > existing.relevance_score:
            by_url[result.url] = result

    return sorted(
        by_url.values(),
        key=lambda item: (SOURCE_TYPE_PRIORITY.get(item.type, 0.5), item.relevance_score),
        reverse=True,
    )[:limit]
