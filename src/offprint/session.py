"""Per-publisher session cookies.

A metered article stays metered for an anonymous request — that is the publisher
honouring its own rules, not a bug to defeat. What *does* work is presenting the
subscription you already pay for: load the cookies for that publisher and the
pipeline sees the full article.

Cookies live in one 0600 JSON file per registrable domain, in Playwright's
storage_state shape, and never leave this machine. The reader proxy is skipped
whenever a session is in play, so a logged-in URL is never handed to a third
party.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .vendor.sites import domain_from_url

DEFAULT_DIR = Path.home() / ".config" / "offprint" / "sessions"
MAX_AGE_DAYS = int(os.getenv("OFFPRINT_SESSION_MAX_AGE_DAYS", "30"))


def sessions_dir() -> Path:
    path = Path(os.getenv("OFFPRINT_SESSION_DIR", DEFAULT_DIR))
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _path(domain: str) -> Path:
    safe = domain.strip().lower().lstrip(".")
    return sessions_dir() / f"{safe}.json"


def _normalise_cookie(cookie: dict, domain: str) -> dict:
    """Fill the fields Playwright requires but a pasted header doesn't carry."""
    host = cookie.get("domain") or domain
    if not host.startswith("."):
        host = f".{host.lstrip('.')}"
    same_site = cookie.get("sameSite")
    if same_site not in ("Strict", "Lax", "None"):
        same_site = "Lax"
    return {
        "name": str(cookie.get("name", "")),
        "value": str(cookie.get("value", "")),
        "domain": host,
        "path": cookie.get("path") or "/",
        "expires": float(cookie.get("expires", -1) or -1),
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", True)),
        "sameSite": same_site,
    }


def save_session(domain: str, cookies: list[dict], *, origins: list | None = None) -> dict:
    domain = domain.strip().lower().lstrip(".")
    if not domain:
        raise ValueError("A domain is required.")
    cleaned = [_normalise_cookie(c, domain) for c in cookies if c.get("name")]
    if not cleaned:
        raise ValueError("No cookies in that session.")

    state = {"cookies": cleaned, "origins": origins or []}
    path = _path(domain)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {
        "domain": domain,
        "cookies": len(cleaned),
        "updated_at": int(time.time()),
    }


def parse_cookie_header(raw: str, domain: str) -> list[dict]:
    """Turn a pasted `Cookie:` header into storage_state cookies."""
    cookies = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        if not name.strip():
            continue
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain})
    return cookies


def load_session(domain: str) -> dict | None:
    path = _path(domain)
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not state.get("cookies"):
        return None
    return state


def session_for_url(url: str) -> dict | None:
    """Session state for the URL's publisher, if one is stored."""
    domain = domain_from_url(url)
    if not domain:
        return None
    return load_session(domain)


def cookie_header(url: str) -> str | None:
    """`name=value; …` for the request's publisher, or None."""
    state = session_for_url(url)
    if not state:
        return None
    pairs = [f"{c['name']}={c['value']}" for c in state.get("cookies", []) if c.get("name")]
    return "; ".join(pairs) if pairs else None


def list_sessions() -> list[dict]:
    rows = []
    for path in sorted(sessions_dir().glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        cookies = state.get("cookies", [])
        updated = int(path.stat().st_mtime)
        age_days = round((time.time() - updated) / 86400, 1)
        rows.append(
            {
                "domain": path.stem,
                "cookies": len(cookies),
                "updated_at": updated,
                "age_days": age_days,
                "stale": age_days > MAX_AGE_DAYS,
                "expired": all(
                    (c.get("expires") or -1) > 0 and c["expires"] < time.time() for c in cookies
                )
                if cookies
                else True,
            }
        )
    return rows


def delete_session(domain: str) -> bool:
    path = _path(domain)
    if path.exists():
        path.unlink()
        return True
    return False


def describe(url: str) -> dict:
    """What the UI shows next to a URL: is there a session for it?"""
    domain = domain_from_url(url or "")
    state = load_session(domain) if domain else None
    return {
        "domain": domain,
        "has_session": bool(state),
        "cookies": len(state.get("cookies", [])) if state else 0,
    }
