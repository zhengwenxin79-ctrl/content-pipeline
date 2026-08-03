from __future__ import annotations

import re
import ssl
import json
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from html import unescape
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schema import RawDocument, SourceRecord


USER_AGENT = "bench-analysis-mvp/0.2"
SSL_CONTEXT = ssl._create_unverified_context()


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def safe_filename(url: str, index: int) -> str:
    parsed = urlparse(url)
    base = f"{parsed.netloc}{parsed.path}".strip("/") or f"source-{index}"
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-")
    return f"{index:02d}-{base[:96]}"


def cache_index_path(raw_dir: Path) -> Path:
    return raw_dir / "raw_index.json"


def load_cache_index(raw_dir: Path) -> dict:
    path = cache_index_path(raw_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_cache_index(raw_dir: Path, records: dict) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_index_path(raw_dir).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def raw_document_from_cache(source: SourceRecord, raw_dir: Path) -> RawDocument | None:
    records = load_cache_index(raw_dir)
    cached = records.get(source.url)
    if not cached:
        return None
    raw_path = Path(cached.get("path", ""))
    text_path = Path(cached.get("text_path", ""))
    if not raw_path.exists() or not text_path.exists():
        return None
    cached["cache_status"] = "hit"
    return RawDocument(**cached)


def cache_raw_document(source: SourceRecord, raw_dir: Path, document: RawDocument) -> None:
    if document.error:
        return
    records = load_cache_index(raw_dir)
    records[source.url] = asdict(document)
    write_cache_index(raw_dir, records)


def github_readme_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc.lower() != "github.com" or len(parts) < 2:
        return []
    owner, repo = parts[:2]
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
    ]


def arxiv_pdf_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    if "arxiv.org" not in parsed.netloc.lower():
        return []
    match = re.search(r"/(?:abs|html)/(?P<identifier>[A-Za-z0-9_.-]+)", parsed.path)
    if not match:
        return []
    identifier = match.group("identifier")
    return [f"https://arxiv.org/pdf/{identifier}"]


def reader_fallback_candidates(url: str) -> list[str]:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"openai.com", "www.openai.com"}:
        return []
    return [f"https://r.jina.ai/http://{url}"]


def extract_text_from_html(content: bytes) -> tuple[str, str]:
    html = content.decode("utf-8", errors="ignore")
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))).strip() if title_match else ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|section|article|li|tr|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text.strip()


def extract_text_from_pdf(content: bytes) -> str:
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf_file:
            pdf_file.write(content)
            pdf_file.flush()
            completed = subprocess.run(
                ["pdftotext", "-layout", pdf_file.name, "-"],
                check=False,
                capture_output=True,
                timeout=30,
            )
        if completed.returncode == 0 and completed.stdout:
            return completed.stdout.decode("utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import fitz
    except ImportError:
        return ""
    document = fitz.open(stream=content, filetype="pdf")
    pages = [page.get_text("text") for page in document]
    return "\n".join(pages)


class FetchResponse:
    def __init__(self, url: str, content: bytes, status_code: int, content_type: str, charset: str | None):
        self.url = url
        self.content = content
        self.status_code = status_code
        self.content_type = content_type
        self.charset = charset

    @property
    def text(self) -> str:
        return self.content.decode(self.charset or "utf-8", errors="ignore")


def _fetch_url(url: str, timeout: int = 20) -> FetchResponse:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        headers = response.headers
        return FetchResponse(
            url=response.geturl(),
            content=response.read(),
            status_code=response.getcode(),
            content_type=headers.get("content-type", ""),
            charset=headers.get_content_charset(),
        )


def _fetch_best_url(source: SourceRecord) -> tuple[str, FetchResponse]:
    readme_candidates = github_readme_candidates(source.url)
    arxiv_candidates = arxiv_pdf_candidates(source.url)
    reader_candidates = reader_fallback_candidates(source.url)
    candidates = [*readme_candidates, *arxiv_candidates, source.url, *reader_candidates]
    last_error = None
    for url in candidates:
        try:
            return url, _fetch_url(url)
        except OSError as exc:
            last_error = exc
    raise OSError(str(last_error))


def fetch_source(source: SourceRecord, raw_dir: Path, index: int) -> RawDocument:
    raw_dir.mkdir(parents=True, exist_ok=True)
    cached = raw_document_from_cache(source, raw_dir)
    if cached is not None:
        return cached
    try:
        fetched_url, response = _fetch_best_url(source)
    except OSError as exc:
        return RawDocument(
            url=source.url,
            source_url=source.url,
            type=source.type,
            path="",
            error=str(exc),
            cache_status="miss",
            fetched_at=utc_now(),
        )

    content_type = response.content_type
    filename = safe_filename(fetched_url, index)
    lower_url = fetched_url.lower()
    if "pdf" in content_type or lower_url.endswith(".pdf"):
        suffix = ".pdf"
    elif "text/plain" in content_type or lower_url.endswith((".md", ".txt")):
        suffix = ".md"
    else:
        suffix = ".html"
    raw_path = raw_dir / f"{filename}{suffix}"
    raw_path.write_bytes(response.content)

    title = source.title
    if suffix == ".pdf":
        text = extract_text_from_pdf(response.content)
    elif "text/plain" in content_type or fetched_url.endswith(".md"):
        text = response.text
        title = title or filename
    else:
        parsed_title, text = extract_text_from_html(response.content)
        title = parsed_title or title

    text_path = raw_dir / f"{filename}.txt"
    text_path.write_text(text, encoding="utf-8", errors="ignore")

    document = RawDocument(
        url=fetched_url,
        source_url=source.url,
        type=source.type,
        path=str(raw_path),
        title=title,
        status_code=response.status_code,
        content_type=content_type,
        text_path=str(text_path),
        text_preview=text[:500],
        cache_status="miss",
        fetched_at=utc_now(),
    )
    cache_raw_document(source, raw_dir, document)
    return document


def fetch_sources(sources: list[SourceRecord], raw_dir: Path, limit: int = 8) -> list[RawDocument]:
    documents = []
    for index, source in enumerate(sources[:limit], start=1):
        documents.append(fetch_source(source, raw_dir, index))
    return documents
