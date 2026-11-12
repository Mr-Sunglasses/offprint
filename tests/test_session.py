"""Session store and its effect on the fetch pipeline."""

import httpx
import pytest

from offprint import session as session_mod
from offprint.fetcher import FetchOptions, fetch_article


@pytest.fixture()
def session_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFPRINT_SESSION_DIR", str(tmp_path / "sessions"))
    return tmp_path / "sessions"


def test_parse_cookie_header():
    cookies = session_mod.parse_cookie_header("a=1; b=2;  junk ; c=3=4", "example.com")
    assert [c["name"] for c in cookies] == ["a", "b", "c"]
    assert cookies[2]["value"] == "3=4"


def test_save_load_roundtrip_and_permissions(session_dir):
    session_mod.save_session("example.com", [{"name": "sid", "value": "xyz"}])
    path = session_dir / "example.com.json"
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600"

    state = session_mod.load_session("example.com")
    assert state["cookies"][0]["value"] == "xyz"
    # Playwright needs these fields filled in
    cookie = state["cookies"][0]
    assert cookie["domain"] == ".example.com"
    assert cookie["path"] == "/"
    assert cookie["sameSite"] in ("Lax", "Strict", "None")


def test_cookie_header_and_describe(session_dir):
    session_mod.save_session(
        "example.com", [{"name": "sid", "value": "xyz"}, {"name": "b", "value": "2"}]
    )
    assert session_mod.cookie_header("https://www.example.com/story") == "sid=xyz; b=2"
    assert session_mod.describe("https://example.com/x")["has_session"] is True
    assert session_mod.describe("https://other.com/x")["has_session"] is False


def test_delete_and_list(session_dir):
    session_mod.save_session("a.com", [{"name": "x", "value": "1"}])
    rows = session_mod.list_sessions()
    assert [r["domain"] for r in rows] == ["a.com"]
    assert session_mod.delete_session("a.com") is True
    assert session_mod.delete_session("a.com") is False
    assert session_mod.list_sessions() == []


def test_save_rejects_empty(session_dir):
    with pytest.raises(ValueError):
        session_mod.save_session("example.com", [])


@pytest.mark.asyncio
async def test_pipeline_sends_cookies_and_skips_proxy(session_dir, monkeypatch):
    session_mod.save_session("example.com", [{"name": "sid", "value": "secret123"}])

    seen_headers: list[dict] = []
    # Short body on purpose: the pipeline keeps escalating, so the proxy stage
    # is reached — and must skip itself because a session is in play.
    html = (
        "<html><body><article><h1>Story</h1>"
        + "<p>"
        + ("Body text long enough to qualify. " * 8)
        + "</p>"
        + "</article></body></html>"
    )

    real_get = httpx.AsyncClient.get

    async def fake_get(self, url, **kwargs):
        seen_headers.append(kwargs.get("headers") or {})
        return httpx.Response(200, text=html, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    steps: list[tuple[str, str, str]] = []

    def progress(stage, status, label):
        steps.append((stage, status, label))

    result = await fetch_article(
        "https://example.com/story",
        FetchOptions(use_archive=False, proxy_fallback=True, skip_browser=True),
        progress=progress,
    )

    assert result.used_session is True
    assert any(h.get("Cookie") == "sid=secret123" for h in seen_headers)
    assert ("proxy", "skipped", "Skipped: your session is in use") in steps
    monkeypatch.setattr(httpx.AsyncClient, "get", real_get)


@pytest.mark.asyncio
async def test_pipeline_ignores_session_when_disabled(session_dir, monkeypatch):
    session_mod.save_session("example.com", [{"name": "sid", "value": "secret123"}])
    seen_headers: list[dict] = []
    html = (
        "<html><body><article><h1>Story</h1><p>"
        + ("Real body text here. " * 40)
        + "</p></article></body></html>"
    )

    async def fake_get(self, url, **kwargs):
        seen_headers.append(kwargs.get("headers") or {})
        return httpx.Response(200, text=html, request=httpx.Request("GET", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await fetch_article(
        "https://example.com/story",
        FetchOptions(use_archive=False, use_session=False),
    )
    assert result.used_session is False
    assert not any("Cookie" in h for h in seen_headers)
