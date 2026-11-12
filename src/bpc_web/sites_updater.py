"""Keep the 936-site strategy table fresh from upstream.

The table is data, not code: bpc-fetch republishes it when Bypass Paywalls Clean
adds sites or changes a site's bypass method. This checks the upstream file,
validates it looks sane, swaps it in and clears the parsed cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .vendor import sites as sites_module

UPSTREAM_URL = os.getenv(
    "BPC_SITES_UPDATE_URL",
    "https://raw.githubusercontent.com/Sophomoresty/bpc-fetch/main/data/sites.js",
)
CHECK_INTERVAL_HOURS = float(os.getenv("BPC_SITES_UPDATE_HOURS", "24"))
MIN_DOMAINS = 500


def _meta_path() -> Path:
    return sites_module.SITES_JS_DEFAULT.parent / "sites_meta.json"


def read_meta() -> dict:
    path = _meta_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_meta(meta: dict) -> None:
    try:
        _meta_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except OSError:
        pass


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _looks_valid(text: str) -> bool:
    if "defaultSites" not in text or "domain:" not in text:
        return False
    return text.count('"domain"') + text.count("domain:") >= MIN_DOMAINS


async def check_and_update(force: bool = False) -> dict:
    """Fetch the upstream table; replace the local copy when it changed."""
    target = sites_module.SITES_JS_DEFAULT
    current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    current_digest = _digest(current) if current else ""

    meta = read_meta()
    now = datetime.now(UTC).isoformat(timespec="seconds")

    if not force and meta.get("last_checked"):
        last = datetime.fromisoformat(meta["last_checked"].replace("Z", "+00:00"))
        age_hours = (datetime.now(UTC) - last).total_seconds() / 3600
        if age_hours < CHECK_INTERVAL_HOURS:
            return {
                "checked": False,
                "reason": f"checked {round(age_hours, 1)}h ago (interval {CHECK_INTERVAL_HOURS}h)",
                "meta": meta,
            }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(UPSTREAM_URL)
    except Exception as exc:
        _write_meta({**meta, "last_checked": now, "last_error": str(exc)[:200]})
        return {"checked": True, "updated": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    if response.status_code != 200:
        _write_meta({**meta, "last_checked": now, "last_error": f"HTTP {response.status_code}"})
        return {"checked": True, "updated": False, "error": f"upstream HTTP {response.status_code}"}

    upstream = response.text
    if not _looks_valid(upstream):
        _write_meta({**meta, "last_checked": now, "last_error": "upstream failed validation"})
        return {
            "checked": True,
            "updated": False,
            "error": "upstream file failed validation — kept the local copy",
        }

    upstream_digest = _digest(upstream)
    if upstream_digest == current_digest:
        _write_meta(
            {
                **meta,
                "last_checked": now,
                "digest": upstream_digest,
                "domains": sites_module.site_count_hint(),
                "last_error": "",
            }
        )
        return {
            "checked": True,
            "updated": False,
            "digest": upstream_digest,
            "reason": "already current",
        }

    # swap it in, keeping one backup
    try:
        if target.exists():
            shutil.copy2(target, target.with_suffix(".js.bak"))
        target.write_text(upstream, encoding="utf-8")
        cache = target.parent / "sites_cache.json"
        cache.unlink(missing_ok=True)
        sites_module.reset_cache()
    except OSError as exc:
        return {"checked": True, "updated": False, "error": f"could not write table: {exc}"}

    domains = len(sites_module.get_sites_map())
    _write_meta(
        {
            "last_checked": now,
            "last_updated": now,
            "digest": upstream_digest,
            "previous_digest": current_digest,
            "domains": domains,
            "last_error": "",
        }
    )
    return {
        "checked": True,
        "updated": True,
        "digest": upstream_digest,
        "previous_digest": current_digest,
        "domains": domains,
        "at": now,
    }


async def auto_update_loop(stop: object | None = None) -> None:
    """Background task: check once at boot, then every CHECK_INTERVAL_HOURS."""
    import asyncio

    while True:
        try:
            result = await check_and_update(force=False)
            if result.get("updated"):
                print(f"[sites] updated strategy table: {result.get('domains')} domains")
        except Exception as exc:
            print(f"[sites] auto-update failed: {exc}")
        await asyncio.sleep(max(CHECK_INTERVAL_HOURS, 1) * 3600)


def seconds_since_check() -> float | None:
    meta = read_meta()
    if not meta.get("last_checked"):
        return None
    try:
        last = datetime.fromisoformat(meta["last_checked"].replace("Z", "+00:00"))
        return time.time() - last.timestamp()
    except Exception:
        return None
