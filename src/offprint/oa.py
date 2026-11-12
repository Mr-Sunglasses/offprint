"""Find a legitimate free copy of a paywalled paper.

Before falling back to a text proxy, ask the open-access indexes: a lot of
"paywalled" research is available as an accepted manuscript in an institutional
repository. OpenAlex needs no key; Unpaywall is used when a contact email is
configured, as their API asks for one.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"
UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"
TIMEOUT = 15.0
CONTACT_EMAIL = os.getenv("OFFPRINT_CONTACT_EMAIL", "").strip()

DOI_IN_URL = re.compile(r"(10\.\d{4,9}/[^\s?#&]+)")


def doi_from_url(url: str) -> str:
    match = DOI_IN_URL.search(urlparse(url).path or "")
    return match.group(1) if match else ""


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    return title[:240]


def _pick_location(work: dict) -> str:
    """Prefer a readable HTML landing page over a PDF."""
    best = work.get("best_oa_location") or {}
    for key in ("url_for_landing_page", "url"):
        value = best.get(key)
        if value:
            return value
    for location in work.get("locations") or []:
        if location.get("is_oa") and location.get("landing_page_url"):
            return location["landing_page_url"]
    return (work.get("open_access") or {}).get("oa_url") or ""


def parse_openalex(payload: dict) -> dict | None:
    for work in payload.get("results") or []:
        if not work.get("is_oa") and not (work.get("open_access") or {}).get("is_oa"):
            continue
        url = _pick_location(work)
        if not url:
            continue
        return {
            "url": url,
            "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
            "title": work.get("display_name") or work.get("title") or "",
            "source": "openalex",
            "host": (work.get("best_oa_location") or {}).get("source", {}).get("display_name", "")
            if isinstance((work.get("best_oa_location") or {}).get("source"), dict)
            else "",
        }
    return None


def parse_unpaywall(payload: dict) -> dict | None:
    if not payload.get("is_oa"):
        return None
    best = payload.get("best_oa_location") or {}
    url = best.get("url_for_landing_page") or best.get("url")
    if not url:
        return None
    return {
        "url": url,
        "doi": payload.get("doi", ""),
        "title": payload.get("title", ""),
        "source": "unpaywall",
        "host": best.get("host_type", ""),
    }


async def find_open_access(
    title: str, url: str = "", client: httpx.AsyncClient | None = None
) -> dict | None:
    """Look for an OA copy by DOI (best) or by title."""
    doi = doi_from_url(url)
    own = client is None
    if own:
        client = httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT)

    try:
        headers = {"User-Agent": "offprint/0.1 (open-access resolver)"}

        if doi:
            try:
                resp = await client.get(OPENALEX, params={"filter": f"doi:{doi}"}, headers=headers)
                if resp.status_code == 200:
                    found = parse_openalex(resp.json())
                    if found:
                        return found
            except Exception:
                pass
            if CONTACT_EMAIL:
                try:
                    resp = await client.get(
                        UNPAYWALL.format(doi=doi), params={"email": CONTACT_EMAIL}, headers=headers
                    )
                    if resp.status_code == 200:
                        found = parse_unpaywall(resp.json())
                        if found:
                            return found
                except Exception:
                    pass
            return None

        cleaned = _clean_title(title)
        if len(cleaned) < 12:
            return None

        try:
            resp = await client.get(
                OPENALEX, params={"search": cleaned, "per-page": 3}, headers=headers
            )
            if resp.status_code == 200:
                found = parse_openalex(resp.json())
                if found and _titles_match(cleaned, found["title"]):
                    return found
        except Exception:
            pass

        if CONTACT_EMAIL:
            try:
                resp = await client.get(
                    CROSSREF, params={"query.bibliographic": cleaned, "rows": 2}, headers=headers
                )
                if resp.status_code == 200:
                    items = (resp.json().get("message") or {}).get("items") or []
                    for item in items:
                        candidate = _clean_title(item.get("title", [""])[0])
                        if item.get("DOI") and _titles_match(cleaned, candidate):
                            oa = await client.get(
                                UNPAYWALL.format(doi=item["DOI"]),
                                params={"email": CONTACT_EMAIL},
                                headers=headers,
                            )
                            if oa.status_code == 200:
                                found = parse_unpaywall(oa.json())
                                if found:
                                    return found
                            break
            except Exception:
                pass
        return None
    finally:
        if own:
            await client.aclose()


def _titles_match(a: str, b: str) -> bool:
    """Cheap guard against matching a different paper entirely."""

    def words(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}

    left, right = words(a), words(b)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= 0.5
