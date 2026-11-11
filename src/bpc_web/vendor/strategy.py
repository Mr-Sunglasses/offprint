"""HTTP header strategies for paywall bypass.

Adapted from bpc-fetch (MIT, Copyright (c) 2026 bpc-fetch contributors). See NOTICE.
"""

from __future__ import annotations

import random

from .sites import SiteStrategy

UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
UA_FACEBOOKBOT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
UA_NORMAL = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

REFERER_GOOGLE = "https://www.google.com/"
REFERER_FACEBOOK = "https://www.facebook.com/"
REFERER_TWITTER = "https://t.co/"

TIMEOUT = 30.0

ACCEPT_HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
)


def build_headers(strategy: SiteStrategy | None) -> dict[str, str]:
    """Turn a site strategy into request headers."""
    headers: dict[str, str] = {
        "Accept": ACCEPT_HTML,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if strategy is None:
        headers["User-Agent"] = UA_GOOGLEBOT
        headers["Referer"] = REFERER_GOOGLE
        return headers

    if strategy.useragent_custom:
        headers["User-Agent"] = strategy.useragent_custom
    else:
        ua = (strategy.useragent or "").lower()
        headers["User-Agent"] = {
            "googlebot": UA_GOOGLEBOT,
            "bingbot": UA_BINGBOT,
            "facebookbot": UA_FACEBOOKBOT,
            "facebook": UA_FACEBOOKBOT,
        }.get(ua, UA_NORMAL)

    ref = (strategy.referer or "").lower()
    if ref == "google":
        headers["Referer"] = REFERER_GOOGLE
    elif ref == "facebook":
        headers["Referer"] = REFERER_FACEBOOK
    elif ref == "twitter":
        headers["Referer"] = REFERER_TWITTER
    elif not strategy.useragent and not strategy.useragent_custom:
        headers["Referer"] = REFERER_GOOGLE

    if strategy.random_ip:
        ip = f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
        headers["X-Forwarded-For"] = ip

    return headers


def googlebot_headers() -> dict[str, str]:
    return {
        "User-Agent": UA_GOOGLEBOT,
        "Referer": REFERER_GOOGLE,
        "Accept": ACCEPT_HTML,
        "Accept-Language": "en-US,en;q=0.9",
    }


# Markers that mean "this HTML is a preview, not the article".
PAYWALL_MARKERS = (
    "log in or create an account to continue",
    "subscribe to continue reading",
    "sign in to continue",
    "create a free account to continue",
    "this article is for subscribers",
    "to read the full story",
    "register for free to continue",
    "already a subscriber? sign in",
    "unlock this article",
    "this content is for subscribers",
    "continue reading with a subscription",
    "you've reached your article limit",
    "you have reached your article limit",
    "become a subscriber to continue",
    "used all of your gift articles",
    "used all of your free articles",
    "reached your limit of free articles",
    "your last free article",
    "free articles remaining",
    "subscribe to keep reading",
    "to continue reading this article",
    "start your free trial",
)

# Markers that mean "a bot guard answered the request, not the site".
BOT_BLOCK_MARKERS = (
    "enable js and disable any ad blocker",
    "please enable javascript",
    "please enable js",
    "just a moment...",
    "checking your browser",
    "attention required!",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "cf-error-details",
    "ddos protection by",
    "request unsuccessful",
    "captcha",
    "403 forbidden",
)


def looks_paywalled(html: str) -> bool:
    lower = html.lower()
    return any(m in lower for m in PAYWALL_MARKERS)


def looks_blocked(html_or_text: str) -> bool:
    lower = html_or_text.lower()
    return any(m in lower for m in BOT_BLOCK_MARKERS)


def has_content(html: str) -> bool:
    """Cheap sanity check: does this HTML have an article-shaped payload?"""
    if not html or len(html) < 500:
        return False
    lower = html.lower()
    if "<article" in lower or "articlebody" in lower or "article-body" in lower:
        return True
    if lower.count("<p") > 3:
        return True
    return len(html) > 8000
