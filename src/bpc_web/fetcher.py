"""The fetch pipeline: cheap HTTP tricks first, then a real browser, then the
Wayback Machine. Only candidates that actually look like an article are kept;
the fullest one wins.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from .browser import BrowserPool, fetch_with_browser
from .extract import extract_article
from .vendor.sites import SiteStrategy, get_sites_map, match_strategy
from .vendor.strategy import (
    TIMEOUT,
    UA_NORMAL,
    build_headers,
    googlebot_headers,
    looks_blocked,
    looks_paywalled,
)

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "smid",
    "share",
    "cmpid",
    "s_cid",
    "at_medium",
    "at_campaign",
    "ns_mchannel",
    "ns_source",
}

ProgressFn = Callable[[str, str, str], Awaitable[None] | None]

MIN_ARTICLE_CHARS = 220  # anything shorter is a stub, not a story
GOOD_ENOUGH_CHARS = 900  # stop escalating at this point


class FetchError(Exception):
    def __init__(self, message: str, *, code: str = "fetch_failed") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# URL handling
# --------------------------------------------------------------------------- #


def normalise_url(raw: str) -> str:
    url = (raw or "").strip().strip("<>\"'")
    if not url:
        raise FetchError("Enter a URL to fetch.", code="empty_url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.hostname or "." not in parsed.hostname:
        raise FetchError(f"'{raw}' does not look like a web address.", code="invalid_url")
    if parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise FetchError("Local addresses are not allowed.", code="invalid_url")

    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query), ""))


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class Attempt:
    stage: str
    label: str
    ok: bool
    status: int = 0
    chars: int = 0
    paywalled: bool = False
    ms: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "label": self.label,
            "ok": self.ok,
            "status": self.status,
            "chars": self.chars,
            "paywalled": self.paywalled,
            "ms": self.ms,
            "note": self.note,
        }


@dataclass
class FetchResult:
    article: dict
    via: str
    strategy: dict
    attempts: list[Attempt] = field(default_factory=list)
    fetched_at: str = ""
    elapsed_ms: int = 0
    confidence: str = "high"

    def to_dict(self) -> dict:
        return {
            "article": self.article,
            "via": self.via,
            "strategy": self.strategy,
            "attempts": [a.to_dict() for a in self.attempts],
            "fetched_at": self.fetched_at,
            "elapsed_ms": self.elapsed_ms,
            "confidence": self.confidence,
        }


@dataclass
class FetchOptions:
    force_browser: bool = False
    skip_browser: bool = False
    use_archive: bool = True
    include_images: bool = True


# --------------------------------------------------------------------------- #
# Candidate quality
# --------------------------------------------------------------------------- #


def _qualifies(article: dict) -> bool:
    """Is this extraction worth showing a human?"""
    text = article.get("text") or ""
    return len(text) >= MIN_ARTICLE_CHARS and not looks_blocked(text[:2000])


def _rank(article: dict) -> int:
    return len(article.get("text") or "")


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


async def fetch_article(
    raw_url: str,
    options: FetchOptions | None = None,
    pool: BrowserPool | None = None,
    progress: ProgressFn | None = None,
) -> FetchResult:
    options = options or FetchOptions()
    started = time.perf_counter()
    url = normalise_url(raw_url)

    async def emit(stage: str, status: str, label: str) -> None:
        if progress is None:
            return
        out = progress(stage, status, label)
        if asyncio.iscoroutine(out):
            await out

    sites = get_sites_map()
    strategy: SiteStrategy | None = match_strategy(url, sites)
    strategy_info = (
        {
            "domain": strategy.domain,
            "name": strategy.name,
            "bypass_type": strategy.bypass_type(),
            "supported": True,
        }
        if strategy
        else {
            "domain": urlparse(url).hostname or "",
            "name": "",
            "bypass_type": "generic",
            "supported": False,
        }
    )
    await emit(
        "resolve",
        "done",
        f"Strategy: {strategy_info['bypass_type']}"
        + (f" · {strategy_info['name']}" if strategy_info["name"] else " · generic fallback"),
    )

    attempts: list[Attempt] = []
    best: dict | None = None
    best_stage = ""

    def consider(html: str, dom: dict | None = None) -> dict:
        art = extract_article(html, url, dom=dom)
        if not options.include_images:
            art["images"] = []
        return art

    def take(art: dict, stage: str) -> None:
        nonlocal best, best_stage
        if not _qualifies(art):
            return
        if best is None or _rank(art) > _rank(best):
            best, best_stage = art, stage

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=TIMEOUT, headers={"Accept-Language": "en-US,en;q=0.9"}
    ) as client:
        # --- 1. direct request, site-specific headers ---------------------- #
        await emit("http", "running", "Direct request with site-specific headers")
        t0 = time.perf_counter()
        try:
            resp = await client.get(url, headers=build_headers(strategy))
        except Exception as exc:
            attempts.append(
                Attempt(
                    "http",
                    "Direct request",
                    False,
                    0,
                    0,
                    False,
                    _ms(t0),
                    f"{type(exc).__name__}: {exc}"[:160],
                )
            )
            await emit("http", "failed", "Direct request failed")
        else:
            paywalled = looks_paywalled(resp.text)
            blocked = looks_blocked(resp.text[:4000])
            art = consider(resp.text)
            attempts.append(
                Attempt(
                    "http",
                    "Direct request",
                    True,
                    resp.status_code,
                    len(art["text"]),
                    paywalled,
                    _ms(t0),
                    "bot guard answered"
                    if blocked
                    else ("paywall markers present" if paywalled else ""),
                )
            )
            await emit("http", "done", f"HTTP {resp.status_code} · {len(art['text']):,} chars")
            if blocked:
                pass  # a bot guard answered: this is not the article
            elif paywalled:
                take(art, "Direct request (partial)")  # keep it, but keep escalating
            else:
                take(art, "Direct request")
                if best is not None and _rank(best) >= GOOD_ENOUGH_CHARS:
                    return _finish(best, best_stage, strategy_info, attempts, started)

        # --- 2. crawler User-Agent ---------------------------------------- #
        await emit("bot", "running", "Retrying as a search-engine crawler")
        t0 = time.perf_counter()
        try:
            resp = await client.get(url, headers=googlebot_headers())
            paywalled = looks_paywalled(resp.text)
            blocked = looks_blocked(resp.text[:4000])
            art = consider(resp.text)
            attempts.append(
                Attempt(
                    "bot",
                    "Crawler User-Agent",
                    True,
                    resp.status_code,
                    len(art["text"]),
                    paywalled,
                    _ms(t0),
                    "bot guard answered"
                    if blocked
                    else ("paywall markers present" if paywalled else ""),
                )
            )
            await emit("bot", "done", f"HTTP {resp.status_code} · {len(art['text']):,} chars")
            if blocked:
                pass
            elif paywalled:
                take(art, "Crawler User-Agent (partial)")
            else:
                take(art, "Crawler User-Agent")
                if best is not None and _rank(best) >= GOOD_ENOUGH_CHARS:
                    return _finish(best, best_stage, strategy_info, attempts, started)
        except Exception as exc:
            attempts.append(
                Attempt(
                    "bot",
                    "Crawler User-Agent",
                    False,
                    0,
                    0,
                    False,
                    _ms(t0),
                    f"{type(exc).__name__}: {exc}"[:160],
                )
            )
            await emit("bot", "failed", "Crawler retry failed")

        # --- 3. headless browser ------------------------------------------ #
        if not options.skip_browser:
            await emit(
                "browser", "running", "Rendering in headless Chromium (paywall scripts blocked)"
            )
            t0 = time.perf_counter()
            try:
                result = (
                    await fetch_with_browser(url, strategy, pool)
                    if pool is not None
                    else await _one_off_browser(url, strategy)
                )
                art = consider(result.html, result.dom)
                chars = len(art["text"])
                attempts.append(
                    Attempt(
                        "browser",
                        "Headless browser",
                        chars > 0,
                        result.status,
                        chars,
                        False,
                        _ms(t0),
                        "DOM extraction" if result.dom else "rendered HTML",
                    )
                )
                await emit(
                    "browser",
                    "done" if chars else "failed",
                    f"{chars:,} chars from rendered DOM"
                    if chars
                    else "No article body in rendered page",
                )
                take(art, "Headless browser")
            except Exception as exc:
                attempts.append(
                    Attempt(
                        "browser",
                        "Headless browser",
                        False,
                        0,
                        0,
                        False,
                        _ms(t0),
                        f"{type(exc).__name__}: {exc}"[:160],
                    )
                )
                await emit("browser", "failed", "Browser render failed")

        # --- 4. Wayback Machine ------------------------------------------- #
        if options.use_archive and (best is None or _rank(best) < GOOD_ENOUGH_CHARS):
            await emit("archive", "running", "Consulting the Wayback Machine")
            t0 = time.perf_counter()
            try:
                resp = await client.get(
                    f"https://web.archive.org/web/2id_/{url}",
                    headers={"User-Agent": UA_NORMAL},
                    timeout=25.0,
                )
                art = consider(resp.text)
                chars = len(art["text"])
                ok = resp.status_code == 200 and chars > 0
                attempts.append(
                    Attempt(
                        "archive",
                        "Wayback Machine",
                        ok,
                        resp.status_code,
                        chars,
                        False,
                        _ms(t0),
                        ""
                        if ok
                        else ("rate limited" if resp.status_code == 429 else "no snapshot"),
                    )
                )
                await emit("archive", "done" if ok else "failed", f"{chars:,} chars from archive")
                if ok:
                    take(art, "Wayback Machine")
            except Exception as exc:
                attempts.append(
                    Attempt(
                        "archive",
                        "Wayback Machine",
                        False,
                        0,
                        0,
                        False,
                        _ms(t0),
                        f"{type(exc).__name__}: {exc}"[:160],
                    )
                )
                await emit("archive", "failed", "Archive lookup failed")

    if best is None:
        detail = "; ".join(
            f"{a.label}: {a.note or f'HTTP {a.status}, {a.chars} chars'}" for a in attempts
        )
        raise FetchError(
            "No readable article found. " + (detail or "The page returned nothing usable."),
            code="no_content",
        )

    return _finish(best, best_stage, strategy_info, attempts, started)


def _finish(article, via, strategy_info, attempts, started) -> FetchResult:
    if article and not article.get("images"):
        article["images"] = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", article.get("markdown", ""))
    words = article.get("word_count", 0)
    confidence = (
        "high"
        if words >= 250 and via in ("Direct request", "Crawler User-Agent")
        else ("medium" if words >= 250 else "low")
    )
    return FetchResult(
        article=article,
        via=via,
        strategy=strategy_info,
        attempts=attempts,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        elapsed_ms=_ms(started),
        confidence=confidence,
    )


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


async def _one_off_browser(url: str, strategy: SiteStrategy | None):
    pool = BrowserPool(max_contexts=1)
    await pool.start()
    try:
        return await fetch_with_browser(url, strategy, pool)
    finally:
        await pool.stop()


def site_count() -> int:
    return len(get_sites_map())
