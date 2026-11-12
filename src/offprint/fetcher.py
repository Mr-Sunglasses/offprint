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
from .proxy import fetch_via_reader_proxy, proxy_enabled, site_name_from_url
from .vendor.sites import SiteStrategy, get_sites_map, match_strategy
from .vendor.strategy import (
    TIMEOUT,
    UA_NORMAL,
    build_headers,
    googlebot_headers,
    looks_blocked,
    looks_metered,
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

    paywalled_preview: bool = False
    winner_stage: str = ""

    def to_dict(self) -> dict:
        return {
            "article": self.article,
            "via": self.via,
            "strategy": self.strategy,
            "attempts": [a.to_dict() for a in self.attempts],
            "fetched_at": self.fetched_at,
            "elapsed_ms": self.elapsed_ms,
            "confidence": self.confidence,
            "paywalled_preview": self.paywalled_preview,
            "winner_stage": self.winner_stage,
        }


@dataclass
class FetchOptions:
    force_browser: bool = False
    skip_browser: bool = False
    use_archive: bool = True
    include_images: bool = True
    proxy_fallback: bool = True


# --------------------------------------------------------------------------- #
# Candidate quality
# --------------------------------------------------------------------------- #


def _qualifies(article: dict) -> bool:
    """Is this extraction worth showing a human?"""
    text = article.get("text") or ""
    return len(text) >= MIN_ARTICLE_CHARS and not looks_blocked(text[:2000])


def _rank(article: dict) -> int:
    return len(article.get("text") or "")


HARD_WALL_MARKERS = (
    "just a moment",
    "cf-mitigated",
    "enable js and disable any ad blocker",
    "checking your browser",
    "verifying you are human",
    "captcha-delivery",
    "datadome",
)


def _is_hard_wall(status: int, html: str) -> bool:
    """Cloudflare/DataDome answered — no header trick will fix this."""
    if status in (401, 403, 429) and looks_blocked(html[:4000]):
        return True
    head = html[:6000].lower()
    return any(marker in head for marker in HARD_WALL_MARKERS)


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
    hard_wall = False
    proxy_tried = False

    async def run_proxy() -> None:
        """Fallback through a text-rendering proxy (fetches from its own IP)."""
        nonlocal best, best_stage, proxy_tried
        if proxy_tried or not options.proxy_fallback or not proxy_enabled():
            return
        proxy_tried = True
        await emit("proxy", "running", "Asking a reader proxy to fetch it from its network")
        t0 = time.perf_counter()
        try:
            art = await fetch_via_reader_proxy(url, client=None)
        except Exception as exc:
            attempts.append(
                Attempt(
                    "proxy",
                    "Text proxy",
                    False,
                    0,
                    0,
                    False,
                    _ms(t0),
                    f"{type(exc).__name__}: {exc}"[:160],
                )
            )
            await emit("proxy", "failed", "Reader proxy failed")
            return

        if not art:
            attempts.append(
                Attempt("proxy", "Text proxy", False, 0, 0, False, _ms(t0), "no content returned")
            )
            await emit("proxy", "failed", "Reader proxy returned nothing")
            return

        art["site"] = art.get("site") or site_name_from_url(url)
        if not options.include_images:
            art["images"] = []
        chars = len(art["text"])
        attempts.append(
            Attempt(
                "proxy", "Text proxy", True, 200, chars, False, _ms(t0), "rendered by r.jina.ai"
            )
        )
        await emit("proxy", "done", f"{chars:,} chars via reader proxy")
        take(art, "Reader proxy")

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
        if not options.force_browser:
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
                hard_wall = _is_hard_wall(resp.status_code, resp.text)
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
                hard_wall = hard_wall or _is_hard_wall(resp.status_code, resp.text)
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

        else:
            await emit("http", "skipped", "Skipped: browser was forced")
            await emit("bot", "skipped", "Skipped: browser was forced")

        # A Cloudflare/DataDome wall won't fall to a local browser: the wall is
        # scoring our IP, so try the proxy first and save 20 seconds.
        if hard_wall and best is None:
            await run_proxy()
            # Behind a Cloudflare/DataDome wall a local browser can't win, and a
            # preview is all a subscription site will ever hand over — stop here
            # instead of burning another 25 seconds to prove it.
            if best is not None and (_rank(best) >= GOOD_ENOUGH_CHARS or hard_wall):
                return _finish(best, best_stage, strategy_info, attempts, started)

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

        if best is None or _rank(best) < GOOD_ENOUGH_CHARS:
            await run_proxy()
            if best is not None and (
                _rank(best) >= GOOD_ENOUGH_CHARS or best.get("paywalled_preview")
            ):
                return _finish(best, best_stage, strategy_info, attempts, started)

        # --- 4. Wayback Machine ------------------------------------------- #
        if options.use_archive and (best is None or _rank(best) < GOOD_ENOUGH_CHARS):
            await emit("archive", "running", "Consulting the Wayback Machine")
            t0 = time.perf_counter()
            archive_note = ""
            for snapshot in await _archive_candidates(client, url):
                try:
                    resp = await client.get(
                        snapshot, headers={"User-Agent": UA_NORMAL}, timeout=25.0
                    )
                except Exception as exc:
                    archive_note = f"{type(exc).__name__}: {exc}"[:160]
                    continue
                if resp.status_code == 429:
                    archive_note = "rate limited"
                    await asyncio.sleep(1.2)
                    continue
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
                        "" if ok else "no snapshot",
                    )
                )
                archive_note = "" if ok else "no snapshot"
                if ok:
                    take(art, "Wayback Machine")
                    await emit("archive", "done", f"{chars:,} chars from archive")
                    break

            if not any(a.stage == "archive" and a.ok for a in attempts):
                if not any(a.stage == "archive" for a in attempts):
                    attempts.append(
                        Attempt(
                            "archive", "Wayback Machine", False, 0, 0, False, _ms(t0), archive_note
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
    preview = bool(article.get("paywalled_preview")) or looks_metered(article.get("markdown", ""))
    confidence = (
        "high"
        if words >= 250 and via in ("Direct request", "Crawler User-Agent")
        else ("medium" if words >= 250 else "low")
    )
    if preview:
        confidence = "low"
    winner_stage = next((a.stage for a in attempts if a.label == via), "")
    return FetchResult(
        article=article,
        via=via,
        strategy=strategy_info,
        attempts=attempts,
        fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        elapsed_ms=_ms(started),
        confidence=confidence,
        paywalled_preview=preview,
        winner_stage=winner_stage,
    )


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


async def _archive_candidates(client: httpx.AsyncClient, url: str) -> list[str]:
    """Snapshot URLs to try, newest first.

    The bare /web/2id_/ path redirects to the latest capture, but it answers
    429 under load; the availability API usually still works and gives us a
    timestamp to pin.
    """
    candidates: list[str] = []
    try:
        resp = await client.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            headers={"User-Agent": UA_NORMAL},
            timeout=15.0,
        )
        if resp.status_code == 200:
            closest = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
            timestamp = closest.get("timestamp")
            if timestamp and closest.get("available"):
                candidates.append(f"https://web.archive.org/web/{timestamp}id_/{url}")
    except Exception:
        pass
    candidates.append(f"https://web.archive.org/web/2id_/{url}")
    return candidates


async def _one_off_browser(url: str, strategy: SiteStrategy | None):
    pool = BrowserPool(max_contexts=1)
    await pool.start()
    try:
        return await fetch_with_browser(url, strategy, pool)
    finally:
        await pool.stop()


def site_count() -> int:
    return len(get_sites_map())
