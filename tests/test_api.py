"""API tests. The fetch pipeline itself is stubbed so tests stay offline."""

from bpc_web.fetcher import FetchResult

FAKE_ARTICLE = {
    "url": "https://example.com/story",
    "title": "A Stubbed Story",
    "author": "T. Est",
    "date": "2026-01-01",
    "site": "Example",
    "markdown": "# A Stubbed Story\n\nBody copy that goes on for a little while.",
    "text": "Body copy that goes on for a little while.",
    "word_count": 9,
    "reading_minutes": 1,
    "excerpt": "Body copy",
    "images": [],
}


def make_result(url: str = "https://example.com/story") -> FetchResult:
    return FetchResult(
        article={**FAKE_ARTICLE, "url": url},
        via="Direct request",
        strategy={
            "domain": "example.com",
            "name": "",
            "bypass_type": "generic",
            "supported": False,
        },
        attempts=[],
        fetched_at="2026-01-01T00:00:00+00:00",
        elapsed_ms=42,
        confidence="high",
    )


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["sites_supported"] == 936


def test_sites_search(client):
    response = client.get("/api/sites", params={"q": "economist"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["supported_total"] == 936
    assert any(site["domain"] == "economist.com" for site in payload["sites"])


def test_lookup_supported_and_unknown(client):
    supported = client.get(
        "/api/lookup", params={"url": "https://www.nytimes.com/2026/01/01/x.html"}
    ).json()
    assert supported["supported"] is True
    assert supported["domain"] == "nytimes.com"

    unknown = client.get("/api/lookup", params={"url": "https://example.com/x"}).json()
    assert unknown["supported"] is False
    assert unknown["bypass_type"] == "generic"


def test_fetch_rejects_bad_url(client):
    response = client.post("/api/fetch", json={"url": "http://localhost:8000/x"})
    assert response.status_code == 422


def test_fetch_job_lifecycle(client, monkeypatch):
    import bpc_web.jobs as jobs_module

    async def fake_fetch_article(url, options=None, pool=None, progress=None):
        if progress:
            progress("resolve", "done", "Strategy: generic")
            progress("http", "done", "HTTP 200 · 60 chars")
        return make_result(url)

    monkeypatch.setattr(jobs_module, "fetch_article", fake_fetch_article)

    created = client.post("/api/fetch", json={"url": "example.com/story"})
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert created.json()["url"] == "https://example.com/story"

    for _ in range(50):
        job = client.get(f"/api/fetch/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
    assert job["status"] == "done", job
    assert job["result"]["article"]["title"] == "A Stubbed Story"
    assert any(step["stage"] == "http" for step in job["steps"])

    markdown = client.get(f"/api/fetch/{job_id}/markdown")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert 'title: "A Stubbed Story"' in markdown.text
    assert "Body copy" in markdown.text


def test_markdown_download_unknown_job(client):
    assert client.get("/api/fetch/nope/markdown").status_code == 404


def test_unknown_api_path_is_404(client):
    assert client.get("/api/definitely-not-a-route").status_code == 404
