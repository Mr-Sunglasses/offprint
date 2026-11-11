"""Reader-proxy fallback (optional).

Some sites (Cloudflare "Just a moment", DataDome) refuse every request from
your IP, and archive.org eventually rate-limits busy IPs too. A text-rendering
proxy fetches from *its* network instead, so the wall never sees you.

This uses Jina Reader's public endpoint (https://r.jina.ai/<url>), which returns
Markdown. It is opt-out: set BPC_READER_PROXY=0 to disable, or flip the toggle
in the UI. The article URL is sent to a third party when this stage runs, which
is why the trace names it explicitly.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

from .extract import finalise_markdown
from .vendor.strategy import looks_metered

PROXY_BASE = os.getenv("BPC_READER_PROXY_URL", "https://r.jina.ai/")
PROXY_TIMEOUT = float(os.getenv("BPC_READER_PROXY_TIMEOUT", "45"))

# Ordered: narrowest selector first, then the whole page.
SELECTORS = ("article", "main", "")

_META_KEYS = {
    "title": "title",
    "url source": "url",
    "published time": "date",
    "description": "excerpt",
    "author": "author",
    "site name": "site",
}


def proxy_enabled() -> bool:
    return os.getenv("BPC_READER_PROXY", "1") not in ("0", "false", "False", "no")


def parse_reader_payload(text: str) -> tuple[dict, str]:
    """Split Jina's `Title: ... / Markdown Content:` envelope."""
    header, marker, body = text.partition("Markdown Content:")
    if not marker:
        return {}, text

    meta: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        mapped = _META_KEYS.get(key.strip().lower())
        if mapped and value.strip():
            meta[mapped] = value.strip()
    return meta, body.lstrip("\n")


def _is_prose(block: str) -> bool:
    first = block.lstrip()[:2]
    if first.startswith(("#", ">", "-", "*", "|")) or re.match(r"^\d+\.", block.lstrip()):
        return False
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", block)
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain).strip()
    return len(plain) >= 120 and bool(re.search(r"[.!?…”\"']\s*$", plain))


def prose_window(markdown: str) -> str:
    """Keep the stretch of the page that is actually the article.

    A full-page render starts with menus and ends with footers; the story is the
    run of long, punctuated paragraphs in the middle. Headings just above it come
    along so section titles survive.
    """
    blocks = [b for b in re.split(r"\n{2,}", markdown) if b.strip()]
    prose = [i for i, block in enumerate(blocks) if _is_prose(block)]
    if len(prose) < 2:
        return markdown

    start, end = prose[0], prose[-1]
    for index in range(start - 1, max(-1, start - 4), -1):
        if blocks[index].lstrip().startswith("#"):
            start = index
            break
    return "\n\n".join(blocks[start : end + 1])


_ERROR_BODY = re.compile(r'^\s*\{\s*"data"\s*:\s*null.{0,400}?"code"', re.DOTALL)


def article_from_reader_payload(text: str, url: str, *, windowed: bool) -> dict | None:
    """Turn a Jina response into an article dict, or None if nothing usable."""
    if _ERROR_BODY.match(text):
        return None

    meta, body = parse_reader_payload(text)

    # The proxy caches by URL; if it hands back a different page, drop it rather
    # than showing one article's body under another article's link.
    source = meta.get("url") or ""
    if source:
        requested_path = urlparse(url).path.rstrip("/")
        source_path = urlparse(source).path.rstrip("/")
        if requested_path and source_path and requested_path != source_path:
            return None
    if windowed:
        body = prose_window(body)

    preview = looks_metered(body)
    markdown = finalise_markdown(body, url)
    if len(markdown) < 200:
        return None

    words = len(re.findall(r"\b[\w'’-]+\b", markdown))
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    plain = re.sub(r"[*_`>#\[\]]", "", plain)

    return {
        "url": url,
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "date": (meta.get("date") or "")[:10],
        "site": meta.get("site") or "",
        "markdown": markdown,
        "text": plain.strip(),
        "word_count": words,
        "reading_minutes": max(1, round(words / 220)),
        "excerpt": " ".join(plain.split())[:280],
        "images": re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", markdown),
        "paywalled_preview": preview,
    }


async def fetch_via_reader_proxy(url: str, client: httpx.AsyncClient | None = None) -> dict | None:
    """Ask the reader proxy for the page, trying a few target selectors."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=True, timeout=PROXY_TIMEOUT)

    headers = {
        "User-Agent": "bpc-fetch-web/0.1 (+https://github.com/Sophomoresty/bpc-fetch)",
        "x-respond-with": "markdown",
        "accept": "text/plain",
    }
    token = os.getenv("BPC_READER_PROXY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        for selector in SELECTORS:
            request_headers = dict(headers)
            if selector:
                request_headers["x-target-selector"] = selector
            try:
                response = await client.get(f"{PROXY_BASE}{url}", headers=request_headers)
            except Exception:
                continue
            if response.status_code != 200:
                continue
            article = article_from_reader_payload(response.text, url, windowed=not selector)
            if article:
                article["title"] = article["title"] or _title_from_markdown(article["markdown"])
                article.update({k: v for k, v in _fill_missing(article).items() if v})
                return article
        return None
    finally:
        if own_client:
            await client.aclose()


def _title_from_markdown(markdown: str) -> str:
    match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _fill_missing(article: dict) -> dict:
    """Reader proxies often omit byline metadata; nothing to add if they did."""
    return {"site": article.get("site") or ""}


def site_name_from_url(url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return host.removeprefix("www.")


__all__ = [
    "article_from_reader_payload",
    "fetch_via_reader_proxy",
    "parse_reader_payload",
    "prose_window",
    "proxy_enabled",
    "site_name_from_url",
]
