"""Site strategy table.

Adapted from bpc-fetch (https://github.com/Sophomoresty/bpc-fetch), MIT License,
Copyright (c) 2026 bpc-fetch contributors. See NOTICE.

The table itself comes from the Bypass Paywalls Clean extension's ``sites.js``
(GPL-3.0, magnolia1234). We only read it as data: a mapping of domain ->
"which header tricks does this site fall for".
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

TWO_PART_TLDS = {
    "co.uk",
    "com.au",
    "co.nz",
    "co.jp",
    "co.kr",
    "com.br",
    "co.za",
    "co.il",
    "com.sg",
    "com.tw",
    "com.ar",
    "com.uy",
    "com.mx",
    "com.pe",
    "com.bo",
    "com.co",
    "co.ke",
    "com.pl",
    "org.il",
    "org.uk",
    "net.au",
    "com.es",
    "co.in",
    "com.cn",
    "com.hk",
    "com.my",
    "com.ph",
    "com.tr",
    "com.ua",
    "co.id",
    "com.pk",
}


def _default_sites_js() -> Path:
    """Locate sites.js: explicit env -> PyInstaller bundle -> package data -> repo."""
    override = os.getenv("BPC_SITES_JS")
    if override:
        return Path(override)
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "data" / "sites.js"
    pkg_data = Path(__file__).parent.parent / "data" / "sites.js"
    if pkg_data.exists():
        return pkg_data
    return Path(__file__).parent.parent.parent.parent / "data" / "sites.js"


SITES_JS_DEFAULT = _default_sites_js()


@dataclass
class SiteStrategy:
    domain: str
    name: str = ""
    useragent: str = ""
    useragent_custom: str = ""
    referer: str = ""
    random_ip: str = ""
    allow_cookies: bool = False
    block_regex: str = ""
    cs_dompurify: bool = False
    group: list[str] = field(default_factory=list)

    def bypass_type(self) -> str:
        if self.useragent_custom:
            return "ua:custom"
        if self.useragent:
            return f"ua:{self.useragent}"
        if self.referer:
            return f"referer:{self.referer}"
        if self.cs_dompurify:
            return "archive"
        if self.block_regex:
            return "block_js"
        return "cookies"

    def needs_browser(self) -> bool:
        """Sites whose paywall lives in JS need a real browser to be read."""
        return self.bypass_type() in ("block_js", "cookies")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bypass_type"] = self.bypass_type()
        return d


# --------------------------------------------------------------------------- #
# JS object literal parsing (no node dependency)
# --------------------------------------------------------------------------- #


def _skip_ws(text: str, i: int, length: int) -> int:
    while i < length and text[i] in " \t\r\n":
        i += 1
    if i < length - 1 and text[i] == "/" and text[i + 1] == "/":
        while i < length and text[i] != "\n":
            i += 1
        return _skip_ws(text, i, length)
    return i


def _read_string(text: str, i: int, length: int) -> tuple[str, int]:
    quote = text[i]
    i += 1
    start = i
    while i < length:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return text[start:i], i + 1
        i += 1
    return text[start:], i


def _read_regex(text: str, i: int, length: int) -> tuple[str, int]:
    i += 1
    start = i
    depth = 0
    while i < length:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
        elif text[i] == "/" and depth == 0:
            body = text[start:i]
            i += 1
            while i < length and text[i].isalpha():
                i += 1
            return body, i
        i += 1
    return text[start:], i


def _read_value(text: str, i: int, length: int) -> tuple:
    if i >= length:
        return None, i
    ch = text[i]
    if ch in "\"'":
        return _read_string(text, i, length)
    if ch == "/":
        return _read_regex(text, i, length)
    if ch == "[":
        return _read_array(text, i, length)
    if ch == "{":
        return _read_object(text, i, length)
    end = i
    while end < length and text[end] not in ",}\r\n":
        end += 1
    raw = text[i:end].strip()
    if raw == "true":
        return True, end
    if raw == "false":
        return False, end
    if raw == "null":
        return None, end
    try:
        return int(raw), end
    except ValueError:
        return raw, end


def _read_array(text: str, i: int, length: int) -> tuple[list, int]:
    i += 1
    items: list = []
    while i < length:
        i = _skip_ws(text, i, length)
        if i >= length or text[i] == "]":
            return items, i + 1
        if text[i] == ",":
            i += 1
            continue
        val, i = _read_value(text, i, length)
        if val is not None:
            items.append(val)
    return items, i


def _read_object(text: str, i: int, length: int) -> tuple[dict, int]:
    i += 1
    props: dict = {}
    while i < length:
        i = _skip_ws(text, i, length)
        if i >= length or text[i] == "}":
            return props, i + 1
        if text[i] == ",":
            i += 1
            continue
        if text[i] in "\"'":
            key, i = _read_string(text, i, length)
        else:
            end = i
            while end < length and text[end] not in ":,} \t\r\n":
                end += 1
            key = text[i:end]
            i = end
        i = _skip_ws(text, i, length)
        if i < length and text[i] == ":":
            i += 1
            i = _skip_ws(text, i, length)
            val, i = _read_value(text, i, length)
            if key == "block_regex" and isinstance(val, str):
                props["block_regex_str"] = val
            else:
                props[key] = val
    return props, i


def _extract_entries(text: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    text = text.strip()
    text = text.removeprefix("{")
    text = text.removesuffix("}")

    length = len(text)
    i = 0
    while i < length:
        i = _skip_ws(text, i, length)
        if i >= length:
            break
        if text[i] == '"':
            key, i = _read_string(text, i, length)
            i = _skip_ws(text, i, length)
            if i < length and text[i] == ":":
                i += 1
                i = _skip_ws(text, i, length)
                if i < length and text[i] == "{":
                    props, i = _read_object(text, i, length)
                    entries[key] = props
                else:
                    _, i = _read_value(text, i, length)
            elif i < length and text[i] == ",":
                i += 1
        else:
            i += 1
    return entries


def _build_strategy(domain: str, name: str, props: dict) -> SiteStrategy:
    return SiteStrategy(
        domain=domain,
        name=name,
        useragent=props.get("useragent", "") or "",
        useragent_custom=props.get("useragent_custom", "") or "",
        referer=props.get("referer", "") or "",
        random_ip=props.get("random_ip", "") or "",
        allow_cookies=bool(props.get("allow_cookies")),
        block_regex=props.get("block_regex_str", "") or "",
        cs_dompurify=bool(props.get("cs_dompurify")),
        group=props.get("group", []) or [],
    )


def parse_sites_js(path: Path | None = None) -> dict[str, SiteStrategy]:
    """Parse sites.js into {domain: SiteStrategy}."""
    path = path or SITES_JS_DEFAULT
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^var defaultSites\s*=\s*", "", text.strip())
    text = re.sub(r";\s*$", "", text)
    text = re.sub(r"^var grouped_sites\s*=\s*\{.*?\};\s*", "", text, flags=re.DOTALL)

    entries = _extract_entries(text)
    result: dict[str, SiteStrategy] = {}
    for name, props in entries.items():
        domain = props.get("domain", "") or ""
        if not domain or domain.startswith("#"):
            if domain.startswith("###"):
                for d in props.get("group", []) or []:
                    result[str(d)] = _build_strategy(str(d), name, props)
            continue
        result[domain] = _build_strategy(domain, name, props)
    return result


_SITES_CACHE: dict[str, SiteStrategy] | None = None


def reset_cache() -> None:
    """Drop the parsed table so the next lookup re-reads sites.js."""
    global _SITES_CACHE
    _SITES_CACHE = None


def site_count_hint() -> int:
    """Cheap domain count straight from the file, without a full parse."""
    try:
        text = SITES_JS_DEFAULT.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count('domain: "')


def get_sites_map(sites_js_path: Path | None = None) -> dict[str, SiteStrategy]:
    """Load (and memoise) the strategy table."""
    global _SITES_CACHE
    if _SITES_CACHE is not None and sites_js_path is None:
        return _SITES_CACHE

    js_path = sites_js_path or SITES_JS_DEFAULT
    cache_path = js_path.parent / "sites_cache.json"
    sites: dict[str, SiteStrategy] | None = None

    if (
        cache_path.exists()
        and js_path.exists()
        and cache_path.stat().st_mtime >= js_path.stat().st_mtime
    ):
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            sites = {k: SiteStrategy(**v) for k, v in data.items()}
        except Exception:
            sites = None

    if sites is None:
        sites = parse_sites_js(js_path)
        try:
            cache_path.write_text(
                json.dumps({k: asdict(v) for k, v in sites.items()}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    if sites_js_path is None:
        _SITES_CACHE = sites
    return sites


def host_from_url(url: str) -> str:
    parsed = urlparse(url if "//" in url else f"//{url}")
    return (parsed.hostname or "").lower()


def domain_from_url(url: str) -> str:
    """Registrable domain for a URL (handles .co.uk-style suffixes)."""
    host = host_from_url(url)
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def match_strategy(url: str, sites: dict[str, SiteStrategy] | None = None) -> SiteStrategy | None:
    """Longest-suffix match of the URL host against the strategy table."""
    sites = sites if sites is not None else get_sites_map()
    host = host_from_url(url)
    if not host:
        return None
    if host in sites:
        return sites[host]
    labels = host.split(".")
    for i in range(1, len(labels) - 1):
        candidate = ".".join(labels[i:])
        if candidate in sites:
            return sites[candidate]
    return None
