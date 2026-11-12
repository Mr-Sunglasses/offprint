"""Article discovery: find recent stories on a site via feed or sitemap.

No search API keys needed — publishers advertise their own feeds, and when they
don't, the sitemap still lists every URL with a lastmod date.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree

from .vendor.sites import domain_from_url, match_strategy
from .vendor.strategy import UA_NORMAL, build_headers

TIMEOUT = 20.0

# Tried only when the homepage doesn't advertise a feed.
FEED_PATHS = (
    "/feed",
    "/rss",
    "/rss.xml",
    "/feed/rss",
    "/feeds/all.rss",
    "/feed/atom",
    "/atom.xml",
    "/index.xml",
    "/rss/feed.xml",
    "/news/rss.xml",
    "/?feed=rss2",
)

# Publishers that keep their feeds off-domain or off the usual paths.
KNOWN_FEEDS = {
    "nytimes.com": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "economist.com": "https://www.economist.com/latest/rss.xml",
    "ft.com": "https://www.ft.com/rss/home",
    "wsj.com": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "washingtonpost.com": "https://feeds.washingtonpost.com/rss/world",
    "theguardian.com": "https://www.theguardian.com/world/rss",
    "bbc.com": "https://feeds.bbci.co.uk/news/rss.xml",
    "theatlantic.com": "https://www.theatlantic.com/feed/all/",
    "newyorker.com": "https://www.newyorker.com/feed/everything",
    "wired.com": "https://www.wired.com/feed/rss",
    "telegraph.co.uk": "https://www.telegraph.co.uk/rss.xml",
    "lemonde.fr": "https://www.lemonde.fr/rss/une.xml",
    "faz.net": "https://www.faz.net/rss/aktuell/",
    "nzz.ch": "https://www.nzz.ch/recent.rss",
    "vanityfair.com": "https://www.vanityfair.com/feed/rss",
    "nationalgeographic.com": "https://www.nationalgeographic.com/feed",
    "independent.co.uk": "https://www.independent.co.uk/news/rss",
    "spiegel.de": "https://www.spiegel.de/schlagzeilen/index.rss",
    "elpais.com": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
    "smh.com.au": "https://www.smh.com.au/rss/feed.xml",
    "zeit.de": "https://newsfeed.zeit.de/index",
    "sueddeutsche.de": "https://rss.sueddeutsche.de/rss/Topthemen",
    "bloomberg.com": "https://feeds.bloomberg.com/markets/news.rss",
    "politico.com": "https://rss.politico.com/politics-news.xml",
    "forbes.com": "https://www.forbes.com/business/feed/",
}

SKIP_URL_PATTERNS = re.compile(
    r"/(video|videos|live|liveblog|podcast|podcasts|gallery|tags?|topics?|authors?|"
    r"search|about|contact|privacy|terms|subscribe|newsletter|account|login)/|"
    r"\.(jpg|jpeg|png|gif|mp4|mp3|pdf|zip)$",
    re.IGNORECASE,
)


class DiscoverError(Exception):
    pass


def _parse_when(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        pass
    try:
        iso = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(iso[:25])
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        return None


def _text(node) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def parse_feed(xml: bytes, base_url: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into a list of article stubs."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(xml, parser=parser)
    except Exception:
        return []
    if root is None:
        return []

    items = [node for node in root.iter() if _localname(node.tag) in ("item", "entry")]

    articles: list[dict] = []
    for item in items:
        link = ""
        for child in item:
            name = _localname(child.tag)
            if name == "link":
                href = child.get("href")
                if href:
                    link = href
                elif _text(child):
                    link = _text(child)
            elif name == "guid" and not link and (_text(child).startswith("http")):
                link = _text(child)
        if not link:
            continue

        title = summary = published = ""
        for child in item:
            name = _localname(child.tag)
            if name == "title":
                title = _text(child)
            elif name in ("description", "summary") or name in ("encoded", "content"):
                summary = summary or _text(child)
            elif name in ("pubdate", "published", "updated", "date"):
                published = published or _text(child)

        articles.append(
            {
                "title": _strip_html(title) or link,
                "url": urljoin(base_url, link),
                "published": (
                    dt.isoformat(timespec="seconds") if (dt := _parse_when(published)) else ""
                ),
                "summary": _strip_html(summary)[:280],
            }
        )
    return articles


def _strip_html(text: str) -> str:
    if not text:
        return ""
    if "<" in text:
        try:
            from lxml import html as lxml_html

            text = lxml_html.fromstring(f"<div>{text}</div>").text_content()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def find_feed_link(page_html: str, base_url: str) -> str:
    for match in re.finditer(r"<link\b[^>]*>", page_html, re.IGNORECASE):
        tag = match.group(0)
        if not re.search(r'type=["\']application/(rss|atom)\+xml', tag, re.IGNORECASE):
            continue
        rel = re.search(r'href=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if rel:
            return urljoin(base_url, rel.group(1))
    return ""


def parse_sitemap(xml: bytes) -> list[dict]:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(xml, parser=parser)
    except Exception:
        return []
    if root is None:
        return []
    rows = []
    for node in root.iter():
        if _localname(node.tag) != "url":
            continue
        loc = lastmod = ""
        for child in node:
            name = _localname(child.tag)
            if name == "loc":
                loc = _text(child)
            elif name == "lastmod":
                lastmod = _text(child)
        if loc:
            rows.append({"url": loc, "published": lastmod, "title": loc.rsplit("/", 1)[-1]})
    return rows


def _looks_like_article(url: str) -> bool:
    parsed = urlparse(url)
    if SKIP_URL_PATTERNS.search(parsed.path or ""):
        return False
    segments = [s for s in (parsed.path or "").split("/") if s]
    return len(segments) >= 2 and len(url) < 300


def _days_from_since(since: str) -> int:
    text = (since or "").strip().lower()
    if text in ("today", "1d", ""):
        return 1
    match = re.fullmatch(r"(\d+)d", text)
    if match:
        return int(match.group(1))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return max(1, (datetime.now(UTC).date() - datetime.fromisoformat(text).date()).days)
        except Exception:
            return 7
    return 7


async def discover_articles(
    site: str,
    since: str = "7d",
    limit: int = 20,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Return recent articles for a domain (or any URL on it)."""
    target = site.strip()
    if "://" not in target:
        target = f"https://{target.lstrip('/')}"
    base = f"{urlparse(target).scheme}://{urlparse(target).netloc}"
    domain = domain_from_url(target)
    strategy = match_strategy(target)

    own = client is None
    if own:
        client = httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT)

    cutoff = datetime.now(UTC) - timedelta(days=_days_from_since(since))
    feed_url = ""
    articles: list[dict] = []
    source = ""

    try:
        # 1. the homepage usually advertises its own feed
        try:
            home = await client.get(base + "/", headers=build_headers(strategy))
            if home.status_code == 200:
                feed_url = find_feed_link(home.text, base + "/")
        except Exception:
            pass

        candidates = [KNOWN_FEEDS.get(domain, "")] if domain in KNOWN_FEEDS else []
        candidates += [feed_url] if feed_url else []
        candidates += [base + path for path in FEED_PATHS]
        seen_feeds: set[str] = set()
        for url in candidates:
            if not url or url in seen_feeds:
                continue
            seen_feeds.add(url)
            try:
                resp = await client.get(url, headers={"User-Agent": UA_NORMAL})
            except Exception:
                continue
            if resp.status_code != 200 or "<" not in resp.text[:400]:
                continue
            parsed = parse_feed(resp.content, base + "/")
            if len(parsed) >= 2:
                articles, feed_url, source = parsed, url, "feed"
                break

        # 2. sitemap fallback (recent URLs by lastmod)
        if not articles:
            for path in (
                "/sitemap.xml",
                "/sitemap_index.xml",
                "/news-sitemap.xml",
                "/sitemap-news.xml",
            ):
                try:
                    resp = await client.get(base + path, headers={"User-Agent": UA_NORMAL})
                except Exception:
                    continue
                if resp.status_code != 200 or "<" not in resp.text[:400]:
                    continue
                rows = parse_sitemap(resp.content)
                if not rows:
                    continue
                good = [row for row in rows if _looks_like_article(row["url"])]
                if good:
                    articles = sorted(good, key=lambda r: r.get("published") or "", reverse=True)
                    source = f"sitemap {path}"
                    break
    finally:
        if own:
            await client.aclose()

    # filter by date when the feed gave us dates; keep undated items
    dated = [a for a in articles if a.get("published")]
    undated = [a for a in articles if not a.get("published")]
    if dated:
        recent = []
        for article in dated:
            when = _parse_when(article["published"])
            if when is None or when >= cutoff:
                recent.append(article)
        articles = recent + undated

    articles = [a for a in articles if _looks_like_article(a["url"])] or articles
    for article in articles:
        if article.get("published"):
            when = _parse_when(article["published"])
            article["published"] = when.date().isoformat() if when else article["published"][:10]

    if not articles:
        raise DiscoverError(
            f"No feed or sitemap found for {domain}. Try pasting the article link directly."
        )

    return {
        "domain": domain,
        "site": (strategy.name if strategy else domain),
        "feed": feed_url,
        "source": source,
        "since": since,
        "count": len(articles[:limit]),
        "articles": articles[:limit],
    }
