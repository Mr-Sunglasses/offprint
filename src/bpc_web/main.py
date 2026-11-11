"""FastAPI application: JSON API + static front end."""

from __future__ import annotations

import ipaddress
import os
import socket
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .browser import BrowserPool
from .fetcher import FetchOptions, normalise_url
from .jobs import JobStore, RateLimiter
from .store import get_store
from .vendor.sites import get_sites_map, match_strategy

STATIC_DIR = Path(__file__).parent / "static"
STARTED_AT = time.time()

MAX_CONCURRENT_FETCHES = int(os.getenv("BPC_MAX_CONCURRENT", "4"))
RATE_LIMIT = int(os.getenv("BPC_RATE_LIMIT", "40"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = BrowserPool(max_contexts=int(os.getenv("BPC_BROWSER_CONTEXTS", "2")))
    app.state.pool = pool
    app.state.jobs = JobStore(pool, max_concurrent=MAX_CONCURRENT_FETCHES)
    app.state.limiter = RateLimiter(limit=RATE_LIMIT)
    yield
    await pool.stop()


app = FastAPI(
    title="bpc-fetch web",
    version=__version__,
    summary="Read paywalled articles as clean Markdown",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "BPC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class FetchRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    force_browser: bool = False
    skip_browser: bool = False
    use_archive: bool = True
    include_images: bool = True
    proxy_fallback: bool = True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request) -> None:
    if os.getenv("BPC_DISABLE_RATE_LIMIT") == "1":
        return
    allowed, retry_after = request.app.state.limiter.allow(_client_key(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many fetches. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def _playwright_ready() -> bool:
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not cache.exists():
        return False
    return any(cache.glob("chromium-*"))


def _slugify(text: str, fallback: str = "article") -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    keep = [c.lower() if c.isalnum() else "-" for c in ascii_text.strip()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug or fallback)[:80]


def _job_or_404(request: Request, job_id: str):
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id.")
    return job


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@app.get("/api/health")
async def health() -> dict:
    sites = get_sites_map()
    return {
        "ok": True,
        "version": __version__,
        "sites_supported": len(sites),
        "playwright_ready": _playwright_ready(),
        "articles_stored": get_store().count(),
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


@app.post("/api/fetch", status_code=202)
async def create_fetch(request: Request, body: FetchRequest) -> dict:
    _enforce_rate_limit(request)
    try:
        url = normalise_url(body.url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    options = FetchOptions(
        force_browser=body.force_browser,
        skip_browser=body.skip_browser,
        use_archive=body.use_archive,
        include_images=body.include_images,
        proxy_fallback=body.proxy_fallback,
    )
    cache_key = (
        f"{url}|{int(body.force_browser)}{int(body.skip_browser)}"
        f"{int(body.use_archive)}{int(body.include_images)}{int(body.proxy_fallback)}"
    )
    job = request.app.state.jobs.submit(url, options, cache_key)
    return {"job_id": job.id, "url": url, "status": job.status}


@app.get("/api/fetch/{job_id}")
async def get_fetch(request: Request, job_id: str) -> dict:
    return _job_or_404(request, job_id).to_dict()


@app.get("/api/fetch/{job_id}/markdown")
async def download_markdown(request: Request, job_id: str) -> Response:
    job = _job_or_404(request, job_id)
    if job.status != "done" or not job.result:
        raise HTTPException(status_code=409, detail="Job is not finished yet.")
    article = job.result["article"]
    filename = f"{_slugify(article.get('title') or '')}.md"
    body = _frontmatter(article) + article["markdown"] + "\n"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _frontmatter(article: dict) -> str:
    def esc(value: str) -> str:
        return str(value).replace('"', '\\"').replace("\n", " ")

    lines = ["---", f'title: "{esc(article.get("title") or "")}"']
    if article.get("author"):
        lines.append(f'author: "{esc(article["author"])}"')
    if article.get("date"):
        lines.append(f'date: "{esc(article["date"])}"')
    if article.get("site"):
        lines.append(f'site: "{esc(article["site"])}"')
    lines.append(f"source: {article.get('url', '')}")
    lines.append("retrieved_by: bpc-fetch-web")
    lines.append("---\n")
    return "\n".join(lines)


@app.get("/api/sites")
async def list_sites(
    q: str = Query("", max_length=120),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    sites = get_sites_map()
    needle = q.strip().lower()
    rows = []
    for domain, strategy in sites.items():
        name = strategy.name or domain
        if needle and needle not in domain.lower() and needle not in name.lower():
            continue
        rows.append(
            {
                "domain": domain,
                "name": name,
                "bypass_type": strategy.bypass_type(),
            }
        )
    rows.sort(key=lambda r: r["domain"])
    return {
        "total": len(rows),
        "supported_total": len(sites),
        "offset": offset,
        "limit": limit,
        "sites": rows[offset : offset + limit],
    }


@app.get("/api/lookup")
async def lookup(url: str = Query(min_length=3, max_length=2048)) -> dict:
    try:
        normalised = normalise_url(url)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    strategy = match_strategy(normalised)
    if strategy is None:
        return {
            "url": normalised,
            "supported": False,
            "domain": urlparse(normalised).hostname or "",
            "bypass_type": "generic",
            "name": "",
        }
    return {
        "url": normalised,
        "supported": True,
        "domain": strategy.domain,
        "name": strategy.name,
        "bypass_type": strategy.bypass_type(),
        "needs_browser": strategy.needs_browser(),
    }


@app.get("/api/articles/{article_id}")
async def get_article(article_id: str) -> dict:
    payload = get_store().get(article_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown article id.")
    return payload


@app.get("/api/articles/{article_id}/markdown")
async def download_stored_markdown(article_id: str) -> Response:
    payload = get_store().get(article_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown article id.")
    article = payload["article"]
    filename = f"{_slugify(article.get('title') or '')}.md"
    return Response(
        content=_frontmatter(article) + article["markdown"] + "\n",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/recent")
async def recent_articles(limit: int = Query(12, ge=1, le=60)) -> dict:
    return {"articles": get_store().recent(limit)}


# --------------------------------------------------------------------------- #
# Image proxy: paywall CDNs often check Referer, browsers sending ours get 403.
# --------------------------------------------------------------------------- #

_IMAGE_CACHE: dict[str, tuple[float, bytes, str]] = {}
_IMAGE_CACHE_MAX = 120
_PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
    )
]


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETS if ip.version == net.version):
            return False
    return True


@app.get("/api/image")
async def proxy_image(url: str = Query(min_length=8, max_length=2048), ref: str = "") -> Response:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="Only http(s) image URLs are proxied.")
    if not _is_public_host(parsed.hostname):
        raise HTTPException(status_code=422, detail="Host is not publicly reachable.")

    now = time.time()
    hit = _IMAGE_CACHE.get(url)
    if hit and now - hit[0] < 3600:
        return Response(
            content=hit[1], media_type=hit[2], headers={"Cache-Control": "public, max-age=3600"}
        )

    referer = ref if ref.startswith("http") else f"{parsed.scheme}://{parsed.netloc}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image fetch failed: {exc}") from exc

    content_type = resp.headers.get("content-type", "")
    if resp.status_code != 200 or not content_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Image unavailable.")
    if len(resp.content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image too large.")

    if len(_IMAGE_CACHE) >= _IMAGE_CACHE_MAX:
        _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)))
    _IMAGE_CACHE[url] = (now, resp.content, content_type)
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Unknown /api/* paths must 404 as JSON, not fall through to the SPA.
@app.api_route(
    "/api/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def api_not_found(rest: str) -> None:
    raise HTTPException(status_code=404, detail=f"Unknown API route: /api/{rest}")


# --------------------------------------------------------------------------- #
# Static front end (built with `npm run build` into src/bpc_web/static)
# --------------------------------------------------------------------------- #

if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
else:

    @app.get("/", include_in_schema=False)
    async def dev_root() -> JSONResponse:
        return JSONResponse(
            {
                "status": "front end not built",
                "hint": "cd web && npm install && npm run build  (or: uv run bpc-web --dev)",
                "api_docs": "/docs",
            }
        )
