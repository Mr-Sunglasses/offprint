"""HTML -> clean Markdown extraction.

Built on trafilatura with a BeautifulSoup fallback. The public shape is a plain
dict so it can be serialised straight to JSON.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .vendor.strategy import PAYWALL_MARKERS

MIN_ARTICLE_CHARS = 220


# --------------------------------------------------------------------------- #
# Cleaning helpers
# --------------------------------------------------------------------------- #

_JUNK_PATTERNS = re.compile(
    r"(piano|paywall|regwall|subscriber-?only|newsletter-?signup|cookie-?(banner|consent)|"
    r"advert(isement)?-?slot|sponsored|promo|social-?share|share-?tools|comments?-?section|"
    r"related-?(articles?|stories)|more-?stories|trending|most-?popular|read-?next|"
    r"outbrain|taboola|recirc|skip-?link|breadcrumb)",
    re.IGNORECASE,
)

_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "form",
    "button",
    "nav",
    "footer",
    "header",
    "aside",
    "svg",
    "figure.ads",
)

_BOILERPLATE_LINE = re.compile(
    r"^(advertisement|subscribe|sign in|log in|already a subscriber.*|"
    r"share (this|on).*|follow us.*|read more.*|listen to this article.*)$",
    re.IGNORECASE,
)

# Whole paragraphs that are consent/privacy chrome, not article copy.
_BOILERPLATE_PARA = re.compile(
    r"(consent management platform|privacy cent(er|re)|these cookies are set by|"
    r"opt out of (tracking|the sale)|gdpr countries|adchoices|tracking technologies|"
    r"we and our \d+ partners|manage (your )?(cookie|privacy) (preferences|settings)|"
    r"you((’|')ve| have) used all of your (gift|free) articles|"
    r"this (is|was) your (last|final) free article|"
    r"to continue reading,? (create|register|subscribe)|"
    r"already have an account\?|"
    r"^sign up (for|to)|^get unlimited access)",
    re.IGNORECASE,
)


def _dedupe_blocks(markdown: str) -> str:
    """Drop repeated blocks — responsive duplicates and repeated labels.

    Publishers ship desktop and mobile copies of the same sentence; trafilatura
    keeps both. Jaccard similarity on word sets catches those without touching
    genuinely distinct paragraphs.
    """
    blocks = [b for b in re.split(r"\n{2,}", markdown) if b.strip()]
    if len(blocks) < 3:
        return markdown

    def words(block: str) -> set[str]:
        plain = re.sub(r"^[#>\-\*\s]+", "", block.lower())
        plain = re.sub(r"[^a-z0-9\s']", " ", plain)
        return {w for w in plain.split() if len(w) > 2}

    kept: list[str] = []
    kept_words: list[set[str]] = []
    for block in blocks:
        current = words(block)
        duplicate = False
        if len(current) >= 2:
            # Short labels ("for DEMOCRATS") need an exact match; sentences need
            # only to be near-identical.
            threshold = 0.99 if len(current) <= 3 else 0.8
            for index in range(max(0, len(kept_words) - 80), len(kept_words)):
                other = kept_words[index]
                if not other:
                    continue
                overlap = len(current & other) / len(current | other)
                if overlap >= threshold:
                    # Keep the longer variant of a near-duplicate pair.
                    if len(block) > len(kept[index]) * 1.15:
                        kept[index] = block
                        kept_words[index] = current
                    duplicate = True
                    break
        if duplicate:
            continue
        kept.append(block)
        kept_words.append(current)
    return "\n\n".join(kept)


def _drop_nav_noise(markdown: str) -> str:
    """Strip clusters of menu/label lines ("Log in", "Skip to content", ...).

    Article prose is long and punctuated; chrome is short and isn't.
    """
    blocks = [b for b in re.split(r"\n{2,}", markdown) if b.strip()]
    if len(blocks) < 5:
        return markdown

    def is_chrome(block: str) -> bool:
        stripped = block.lstrip()
        if stripped.startswith(("#", ">", "-", "*", "|", "1.")):
            return False
        plain = re.sub(r"^[#>\-\*\s]+", "", block).strip()
        return not (len(plain) >= 45 or re.search(r"[.!?…]\s*$", plain))

    chrome = sum(1 for b in blocks if is_chrome(b))
    if chrome / len(blocks) < 0.3:
        return markdown
    return "\n\n".join(b for b in blocks if not is_chrome(b))


def _drop_boilerplate_paragraphs(markdown: str) -> str:
    kept: list[str] = []
    for block in re.split(r"\n{2,}", markdown):
        snippet = block.strip()
        if not snippet:
            continue
        plain = re.sub(r"^[#>\-\*\s]+", "", snippet)
        if len(plain) < 400 and _BOILERPLATE_PARA.search(plain):
            continue
        kept.append(snippet)
    return "\n\n".join(kept)


def strip_paywall_text(text: str) -> str:
    """Cut trailing login/subscribe prompts that leak into the body."""
    if not text:
        return ""
    lower = text.lower()
    cut = len(text)
    for marker in PAYWALL_MARKERS:
        idx = lower.find(marker)
        if idx > 120:  # ignore matches in the first sentence (legit prose)
            cut = min(cut, idx)
    text = text[:cut]

    # Drop dangling CTA lines at the tail.
    lines = text.rstrip().split("\n")
    while lines:
        tail = lines[-1].strip().strip("*_#>-").strip()
        if not tail or _BOILERPLATE_LINE.match(tail) or len(tail) < 3:
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _absolutise_images(markdown: str, base_url: str) -> str:
    def repl(match: re.Match) -> str:
        alt, src = match.group(1), match.group(2).strip()
        if not src or src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        return f"![{alt}]({urljoin(base_url, src)})"

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", repl, markdown)


def _tidy_markdown(markdown: str) -> str:
    md = markdown.replace("\r\n", "\n")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"^\s*Advertisement\s*$", "", md, flags=re.MULTILINE | re.IGNORECASE)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


def _meta(html: str, *names: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            value = str(tag["content"]).strip()
            if value:
                return value
    return ""


def _json_ld(html: str) -> dict:
    """Pull title/author/date out of schema.org Article JSON-LD, when present."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            candidates = node.get("@graph", []) if "@graph" in node else [node]
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                kind = str(cand.get("@type", "")).lower()
                if "article" in kind or kind in ("newsarticle", "reportage", "blogposting"):
                    author = cand.get("author")
                    if isinstance(author, list) and author:
                        author = author[0]
                    if isinstance(author, dict):
                        author = author.get("name", "")
                    return {
                        "title": str(cand.get("headline") or cand.get("name") or "").strip(),
                        "author": str(author or "").strip(),
                        "date": str(cand.get("datePublished") or "").strip()[:10],
                        "description": str(cand.get("description") or "").strip(),
                        "site": str(
                            (cand.get("publisher") or {}).get("name", "")
                            if isinstance(cand.get("publisher"), dict)
                            else ""
                        ).strip(),
                        "body": str(cand.get("articleBody") or "").strip(),
                    }
    return {}


def json_ld_body(html: str) -> str:
    """Full body text from schema.org JSON-LD.

    Many metered sites ship the whole article there for syndication/SEO while
    the rendered page stops at the meter. When present, it's the cleanest win.
    """
    body = _json_ld(html).get("body") or ""
    if len(body) < 600:
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n{1,}", body) if p.strip()]
    if len(paragraphs) <= 1:
        # A single blob: split on sentence groups to keep paragraphs readable.
        sentences = re.split(r"(?<=[.!?])\s+", body)
        paragraphs, chunk = [], []
        for sentence in sentences:
            chunk.append(sentence)
            if sum(len(s) for s in chunk) > 320:
                paragraphs.append(" ".join(chunk))
                chunk = []
        if chunk:
            paragraphs.append(" ".join(chunk))
    return "\n\n".join(paragraphs)


def _title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    if h1:
        text = " ".join(h1.get_text(" ", strip=True).split())
        if 8 < len(text) < 300:
            return text
    if soup.title and soup.title.string:
        raw = " ".join(soup.title.string.split())
        for sep in (" | ", " - ", " — ", " :: "):
            if sep in raw:
                raw = raw.split(sep)[0]
        return raw.strip()
    return ""


# --------------------------------------------------------------------------- #
# Fallback extractor
# --------------------------------------------------------------------------- #


def _trafilatura_markdown(html: str, url: str) -> str:
    """Main extractor: trafilatura's markdown output (links, images, headings)."""
    try:
        import trafilatura

        out = trafilatura.extract(
            html,
            url=url,
            include_images=True,
            include_links=True,
            include_tables=True,
            include_formatting=True,
            output_format="markdown",
            favor_recall=True,
            deduplicate=True,
        )
        if out and len(out.strip()) > MIN_ARTICLE_CHARS:
            return out
        # Older trafilatura builds only ship txt/json/xml/csv.
        fallback = trafilatura.extract(
            html,
            url=url,
            include_images=True,
            include_links=True,
            output_format="txt",
            favor_recall=True,
        )
        if fallback and len(fallback.strip()) > MIN_ARTICLE_CHARS:
            from markdownify import markdownify as md

            return md(fallback, heading_style="ATX")
        return out or fallback or ""
    except Exception:
        return ""


def _bs4_extract(html: str, url: str) -> tuple[str, str]:
    """Last-resort extractor: biggest text block wins. Returns (title, markdown)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(attrs={"class": _JUNK_PATTERNS}):
        tag.decompose()

    container = (
        soup.find("article")
        or soup.find(attrs={"itemprop": "articleBody"})
        or soup.find(
            class_=re.compile(
                r"article[-_]?(body|content|text)|story[-_]?body|post[-_]content|entry[-_]content",
                re.IGNORECASE,
            )
        )
        or soup.find("main")
        or soup.body
    )
    if container is None:
        return "", ""

    blocks: list[str] = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li", "figcaption"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if len(text) < 2:
            continue
        if el.name in ("h2", "h3"):
            blocks.append(f"## {text}")
        elif el.name == "blockquote":
            blocks.append(f"> {text}")
        elif el.name == "li":
            blocks.append(f"- {text}")
        else:
            blocks.append(text)

    markdown = _tidy_markdown("\n\n".join(blocks))
    return _title_from_html(html), markdown


def _best_block_text(html: str) -> str:
    """Length of the most plausible article text, used for scoring candidates."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 40]
    return "\n\n".join(paragraphs)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_article(html: str, url: str, dom: dict | None = None) -> dict:
    """Extract one article. Returns a dict with markdown + metadata."""
    if not html and not dom:
        return _empty(url)

    markdown = ""
    title = ""
    author = ""
    date = ""
    site = ""

    if dom and (dom.get("text") or "").strip():
        paragraphs = [p.strip() for p in (dom.get("text") or "").split("\n") if p.strip()]
        markdown = _tidy_markdown("\n\n".join(paragraphs))
        title = (dom.get("title") or "").strip()
        author = (dom.get("author") or "").strip()
        date = (dom.get("date") or "").strip()

    if html:
        meta = _json_ld(html) or {}
        title = title or meta.get("title", "") or _title_from_html(html)
        author = (
            author
            or meta.get("author", "")
            or _meta(html, "author", "article:author", "sailthru.author")
        )
        date = (
            date
            or meta.get("date", "")
            or _meta(html, "article:published_time", "datePublished", "publish-date", "date")[:10]
        )
        site = meta.get("site", "") or _meta(html, "og:site_name")
        if not markdown:
            markdown = _trafilatura_markdown(html, url) or _bs4_extract(html, url)[1]

        # A metered page stops mid-story even though the body sits in JSON-LD.
        inline_body = json_ld_body(html)
        if inline_body and len(inline_body) > len(markdown) * 1.15:
            markdown = inline_body

    markdown = _dedupe_blocks(_drop_nav_noise(_drop_boilerplate_paragraphs(markdown)))
    markdown = strip_paywall_text(_absolutise_images(markdown, url))
    markdown = _tidy_markdown(markdown)

    words = len(re.findall(r"\b[\w'’-]+\b", markdown))
    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    plain = re.sub(r"[*_`>#\[\]]", "", plain)

    return {
        "url": url,
        "title": title or urlparse(url).path.rsplit("/", 1)[-1].replace("-", " ").title(),
        "author": author,
        "date": date,
        "site": site or (urlparse(url).hostname or "").removeprefix("www."),
        "markdown": markdown,
        "text": plain.strip(),
        "word_count": words,
        "reading_minutes": max(1, round(words / 220)),
        "excerpt": " ".join(plain.split())[:280],
        "images": re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", markdown),
    }


def score_candidate(article: dict) -> int:
    """Rank extraction attempts so the fullest body wins."""
    text = article.get("text") or ""
    score = len(text)
    if len(text) < MIN_ARTICLE_CHARS:
        score -= 5000
    return score


def _empty(url: str) -> dict:
    return {
        "url": url,
        "title": "",
        "author": "",
        "date": "",
        "site": "",
        "markdown": "",
        "text": "",
        "word_count": 0,
        "reading_minutes": 0,
        "excerpt": "",
        "images": [],
    }
