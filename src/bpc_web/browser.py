"""Headless-browser fallback for sites whose paywall is enforced in JavaScript.

Launches Chromium via Playwright, aborts the request patterns that gate the
article, strips the overlay nodes, then reads the rendered DOM.
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass

from playwright.async_api import Browser, Playwright, async_playwright

from .vendor.sites import SiteStrategy
from .vendor.strategy import UA_NORMAL

BROWSER_TIMEOUT_MS = 35_000

# Third-party paywall / metering SDKs: no article is worth loading them.
PAYWALL_PROVIDERS = (
    "piano.io",
    "tinypass.com",
    "poool.fr",
    "zephr.com",
    "pelcro.com",
    "sophi.io",
    "onecount.net",
    "matheranalytics",
    "cxense.com",
    "qiota.com",
    "blueconic.net",
    "evolok.net",
    "pelcro",
    "po.st",
)

BLOCKED_RESOURCE_TYPES = {"font", "media"}

# Runs in every frame before page scripts: removes the obvious automation tells.
# (Cloudflare/DataDome score IP reputation too — this only helps the fingerprint
# half of the problem, which is why BPC_HEADFUL exists.)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
window.chrome = window.chrome || { runtime: {}, loadTimes: () => {}, csi: () => {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (params) => (
    params && params.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery.call(window.navigator.permissions, params)
  );
}
"""

CHALLENGE_TITLES = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verifying you are human",
)
CHALLENGE_WAIT_MS = int(os.getenv("BPC_CHALLENGE_WAIT_MS", "14000"))


@dataclass
class BrowserResult:
    html: str
    status: int
    dom: dict | None
    final_url: str


def _regex_to_glob(regex_part: str) -> str:
    """Best-effort: turn a BPC block_regex fragment into a Playwright glob."""
    s = regex_part.replace(r"\.", ".").replace(r"\/", "/")
    s = re.sub(r"\.\+", "*", s)
    s = re.sub(r"\.\*", "*", s)
    s = re.sub(r"\([^)]*\)", "*", s)
    s = re.sub(r"\[[^\]]*\]", "?", s)
    s = re.sub(r"[\\^$]", "", s)
    s = s.strip()
    if not s or s == "*":
        return ""
    if not s.startswith("*"):
        s = "**/" + s
    if not s.endswith("*"):
        s = s + "*"
    return s


def route_patterns(strategy: SiteStrategy | None) -> list[str]:
    """Glob patterns to abort before the page can gate itself."""
    patterns = [f"**/*{p}*" for p in PAYWALL_PROVIDERS]
    if strategy and strategy.block_regex:
        for part in re.split(r"\|", strategy.block_regex):
            glob = _regex_to_glob(part.strip().strip("()"))
            if glob:
                patterns.append(glob)
    return patterns


# Runs inside the page: kill overlays, unhide the body, mark the article body.
_DOM_CLEANUP_JS = r"""
() => {
  const OVERLAY = '[class*="paywall" i], [id*="paywall" i], [class*="regwall" i], '
    + '[class*="piano" i], [class*="subscriber-only" i], [class*="gate" i], '
    + '[class*="meter" i], [data-paywall], [data-testid*="paywall" i], '
    + '[data-component*="paywall" i], .tp-modal, .tp-backdrop, #piano-inline';
  document.querySelectorAll('nav, header, footer, aside, [role="navigation"], '
    + '[role="banner"], [role="contentinfo"], [role="complementary"], '
    + '[class*="breadcrumb" i], [class*="menu" i], [class*="nav-" i], '
    + '[class*="social-share" i], [class*="share-tools" i]').forEach(el => el.remove());

  const CONSENT = '#onetrust-consent-sdk, #CybotCookiebotDialog, .ot-sdk-container, '
    + '[id*="consent" i], [class*="consent-banner" i], [id*="gdpr" i], '
    + '[class*="cookie-banner" i], [id*="cookie-banner" i], [aria-label*="cookie" i], '
    + 'dialog[open], [class*="newsletter-modal" i], [class*="registration-wall" i]';
  document.querySelectorAll(CONSENT).forEach(el => el.remove());

  document.querySelectorAll(OVERLAY).forEach(el => {
    const style = window.getComputedStyle(el);
    if (style.position === 'fixed' || style.position === 'absolute' ||
        el.className.toString().toLowerCase().includes('paywall') ||
        el.className.toString().toLowerCase().includes('modal')) {
      el.remove();
    } else {
      el.style.display = 'none';
    }
  });

  const BODY = 'article, [itemprop="articleBody"], [data-article], .article-body, '
    + '.article__body, .story-body, .post-content, .entry-content, [data-component="body"]';
  document.querySelectorAll(BODY).forEach(el => {
    el.style.setProperty('overflow', 'visible', 'important');
    el.style.setProperty('max-height', 'none', 'important');
    el.style.setProperty('height', 'auto', 'important');
    el.style.setProperty('opacity', '1', 'important');
    el.removeAttribute('hidden');
    el.querySelectorAll('[class*="truncat" i], [style*="max-height"]').forEach(n => {
      n.style.setProperty('max-height', 'none', 'important');
      n.style.setProperty('overflow', 'visible', 'important');
    });
  });

  document.body.style.setProperty('overflow', 'auto', 'important');
  document.documentElement.style.setProperty('overflow', 'auto', 'important');
  document.querySelectorAll('html, body').forEach(el => {
    el.style.setProperty('position', 'static', 'important');
  });
  return true;
}
"""

# Runs inside the page: read the rendered article out of the DOM.
_DOM_EXTRACT_JS = r"""
() => {
  const CANDIDATES = [
    '[itemprop="articleBody"]', 'article [data-body-id]', 'article .article-body',
    '.article-body', '.article__body', '.story-body', '.story-text', '.article-text',
    '.post-content', '.entry-content', '[data-component="body"]', 'article', 'main'
  ];
  let container = null;
  let best = 0;
  for (const sel of CANDIDATES) {
    document.querySelectorAll(sel).forEach(el => {
      const len = (el.innerText || '').length;
      if (len > best) { best = len; container = el; }
    });
    if (container && best > 800) break;
  }
  if (!container) return null;

  const blocks = [];
  container.querySelectorAll('h2, h3, h4, p, blockquote, li, figcaption, pre').forEach(el => {
    if (el.closest('figure') && el.tagName === 'FIGCAPTION') { /* keep */ }
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (text.length < 2) return;
    if (/^(advertisement|subscribe|sign in|log in)$/i.test(text)) return;
    if (text.length < 300 && /(consent management|cookies? (are|is) (set|used)|privacy (center|policy)|gdpr|adchoices|tracking technologies|opt out of)/i.test(text)) return;
    if (el.tagName === 'H2') blocks.push('## ' + text);
    else if (el.tagName === 'H3') blocks.push('### ' + text);
    else if (el.tagName === 'H4') blocks.push('#### ' + text);
    else if (el.tagName === 'BLOCKQUOTE') blocks.push('> ' + text);
    else if (el.tagName === 'LI') blocks.push('- ' + text);
    else blocks.push(text);
  });

  // Images that sit inside the article container.
  const images = [];
  container.querySelectorAll('img').forEach(img => {
    const src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
    if (!src || src.startsWith('data:')) return;
    if (/pixel|tracking|sprite|logo|icon|avatar|badge|emoji/i.test(src)) return;
    if (images.includes(src)) return;
    images.push(src);
  });

  const meta = (sel) => document.querySelector(sel)?.content || '';
  const title = document.querySelector('h1')?.innerText?.trim()
    || meta('meta[property="og:title"]')
    || document.title.split('|')[0].trim();

  return {
    title,
    text: blocks.join('\n\n'),
    images,
    author: meta('meta[name="author"]') || meta('meta[property="article:author"]')
      || document.querySelector('[rel="author"], [class*="byline" i]')?.innerText?.trim() || '',
    date: meta('meta[property="article:published_time"]') || meta('meta[name="date"]') || '',
    scrollHeight: document.body.scrollHeight,
  };
}
"""


class BrowserPool:
    """One Chromium process, a small pool of contexts, hard concurrency cap."""

    def __init__(self, max_contexts: int = 2) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._sem = asyncio.Semaphore(max_contexts)
        self._lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def start(self) -> None:
        async with self._lock:
            if self.started:
                return
            self._pw = await async_playwright().start()
            headful = os.getenv("BPC_HEADFUL", "0") not in ("0", "false", "no")
            self._browser = await self._pw.chromium.launch(
                headless=not headful,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                ],
            )

    async def stop(self) -> None:
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    @asynccontextmanager
    async def page(self, user_agent: str = UA_NORMAL):
        await self.start()
        async with self._sem:
            assert self._browser is not None
            context = await self._browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1440, "height": 1200},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                java_script_enabled=True,
            )
            context.set_default_timeout(BROWSER_TIMEOUT_MS)
            await context.add_init_script(STEALTH_JS)
            page = await context.new_page()
            try:
                yield page
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass


async def _wire_blocking(page, strategy: SiteStrategy | None) -> None:
    async def handler(route):
        try:
            if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass

    await page.route("**/*", handler)
    for pattern in route_patterns(strategy):
        try:
            await page.route(pattern, lambda route: asyncio.ensure_future(route.abort()))
        except Exception:
            pass


async def fetch_with_browser(
    url: str,
    strategy: SiteStrategy | None,
    pool: BrowserPool,
) -> BrowserResult:
    """Render the page, strip the paywall, return rendered HTML + DOM extract."""
    ua = strategy.useragent_custom if strategy and strategy.useragent_custom else UA_NORMAL

    async with pool.page(user_agent=ua) as page:
        await _wire_blocking(page, strategy)

        status = 0
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS
            )
            status = response.status if response else 0
        except Exception:
            status = 0

        try:
            await page.wait_for_selector(
                "article, [itemprop='articleBody'], .article-body, .story-body, .post-content, main",
                timeout=8000,
            )
        except Exception:
            pass

        # A JS interstitial ("Just a moment…") can clear itself; give it time.
        try:
            title = (await page.title()).lower()
            body_len = await page.evaluate("document.body ? document.body.innerText.length : 0")
            if any(marker in title for marker in CHALLENGE_TITLES) or body_len < 400:
                waited = 0
                while waited < CHALLENGE_WAIT_MS:
                    await page.wait_for_timeout(2000)
                    waited += 2000
                    body_len = await page.evaluate(
                        "document.body ? document.body.innerText.length : 0"
                    )
                    title = (await page.title()).lower()
                    if body_len > 1500 and not any(m in title for m in CHALLENGE_TITLES):
                        break
        except Exception:
            pass

        # Nudge lazy loaders: walk down the page, then back up.
        try:
            await page.evaluate(
                "async () => { const step = window.innerHeight;"
                " for (let y = 0; y < document.body.scrollHeight; y += step) {"
                "   window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); }"
                " window.scrollTo(0, 0); }"
            )
        except Exception:
            pass

        try:
            await page.evaluate(_DOM_CLEANUP_JS)
        except Exception:
            pass

        await page.wait_for_timeout(600)

        dom = None
        try:
            dom = await page.evaluate(_DOM_EXTRACT_JS)
        except Exception:
            dom = None

        html = ""
        try:
            html = await page.content()
        except Exception:
            html = ""

        return BrowserResult(
            html=html,
            status=status or 200,
            dom=dom,
            final_url=page.url or url,
        )
