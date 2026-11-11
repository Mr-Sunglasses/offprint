"""Tests for the site table parser and URL helpers."""

from bpc_web.fetcher import FetchError, normalise_url
from bpc_web.vendor.sites import (
    domain_from_url,
    get_sites_map,
    match_strategy,
    parse_sites_js,
)


def test_table_has_all_sites():
    sites = get_sites_map()
    assert len(sites) == 936
    assert "economist.com" in sites
    assert "nytimes.com" in sites
    # group entries must fan out to their members
    assert sites["economist.com"].name
    assert sites["economist.com"].bypass_type().startswith("ua")


def test_strategy_types_are_known():
    known = {
        "ua:custom",
        "ua:googlebot",
        "ua:bingbot",
        "ua:facebookbot",
        "referer:google",
        "block_js",
        "archive",
        "cookies",
    }
    types = {strategy.bypass_type() for strategy in get_sites_map().values()}
    assert types <= known, types - known


def test_match_strategy_uses_subdomains():
    sites = get_sites_map()
    match = match_strategy("https://www.nytimes.com/2024/01/01/us/x.html", sites)
    assert match is not None and match.domain == "nytimes.com"


def test_match_strategy_returns_none_for_unknown():
    assert match_strategy("https://example.com/a", get_sites_map()) is None


def test_domain_from_url_handles_two_part_tlds():
    assert domain_from_url("https://www.telegraph.co.uk/business/x") == "telegraph.co.uk"
    assert domain_from_url("https://www.smh.com.au/national/x") == "smh.com.au"
    assert domain_from_url("https://edition.cnn.com/x") == "cnn.com"


def test_parse_is_deterministic():
    sites = get_sites_map()
    fresh = parse_sites_js()
    assert len(fresh) == len(sites)
    assert fresh["ft.com"].bypass_type() == sites["ft.com"].bypass_type()


def test_normalise_url_adds_scheme_and_drops_tracking():
    assert normalise_url("example.com/story") == "https://example.com/story"
    cleaned = normalise_url("https://news.site/a?utm_source=x&id=7&fbclid=abc")
    assert cleaned == "https://news.site/a?id=7"


def test_normalise_url_rejects_junk():
    for bad in ("", "   ", "http://localhost/x", "http://127.0.0.1/x", "notaurl"):
        try:
            normalise_url(bad)
        except FetchError:
            continue
        raise AssertionError(f"{bad!r} should have been rejected")
