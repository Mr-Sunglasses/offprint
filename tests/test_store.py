"""SQLite article store tests."""

from bpc_web.store import ArticleStore

PAYLOAD = {
    "article": {
        "url": "https://example.com/story",
        "title": "A Stored Story",
        "site": "Example",
        "author": "T. Est",
        "date": "2026-02-02",
        "word_count": 420,
        "markdown": "# A Stored Story\n\nBody copy that goes on for a while.",
        "images": [],
    },
    "via": "Reader proxy",
    "strategy": {"domain": "example.com", "bypass_type": "block_js"},
    "attempts": [{"stage": "proxy", "ok": True}],
    "confidence": "low",
    "paywalled_preview": True,
    "fetched_at": "2026-02-02T10:00:00+00:00",
    "elapsed_ms": 4321,
}


def test_save_and_get(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    article_id = store.save(PAYLOAD, options_key="k1")

    stored = store.get(article_id)
    assert stored is not None
    assert stored["article"]["title"] == "A Stored Story"
    assert stored["article"]["word_count"] == 420
    assert stored["via"] == "Reader proxy"
    assert stored["strategy"]["bypass_type"] == "block_js"
    assert stored["paywalled_preview"] is True
    assert stored["article_id"] == article_id


def test_find_by_url_respects_options(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    article_id = store.save(PAYLOAD, options_key="k1")

    found = store.find_by_url("https://example.com/story", "k1")
    assert found is not None and found["article_id"] == article_id
    assert store.find_by_url("https://example.com/story", "different") is None
    assert store.find_by_url("https://example.com/other", "k1") is None


def test_recent_lists_newest_first(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    first = store.save(PAYLOAD, options_key="k1")
    second = store.save({**PAYLOAD, "article": {**PAYLOAD["article"], "title": "Second"}}, "k1")

    rows = store.recent(5)
    assert [row["article_id"] for row in rows][:2] == [second, first]
    assert rows[0]["title"] == "Second"
    assert store.count() == 2


def test_unknown_id_returns_none(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    assert store.get("doesnotexist") is None
