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


def test_full_text_search(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    store.save(PAYLOAD, options_key="k1")
    store.save(
        {
            **PAYLOAD,
            "article": {
                **PAYLOAD["article"],
                "title": "Robotics Weekly",
                "markdown": "# Robotics Weekly\n\nThe microduck robot learned to fetch the cat's toys.",
            },
        },
        options_key="k2",
    )

    hits = store.search("robotics")
    assert len(hits) == 1
    assert hits[0]["title"] == "Robotics Weekly"
    assert store.search("microduck")[0]["article_id"] == hits[0]["article_id"]
    assert store.search("") == []
    assert store.search("nonexistentterm") == []


def test_reindex_backfills_missing_rows(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    article_id = store.save(PAYLOAD, options_key="k1")
    # simulate a pre-FTS row
    with store._connect() as conn:
        conn.execute("DELETE FROM articles_fts WHERE article_id = ?", (article_id,))
    assert store.search("Stored") == []
    assert store.reindex_missing() == 1
    assert store.search("Stored")


def test_domain_stats_aggregate(tmp_path):
    store = ArticleStore(tmp_path / "articles.db")
    payload = {
        **PAYLOAD,
        "via": "Headless browser",
        "winner_stage": "browser",
        "attempts": [
            {"stage": "http", "label": "Direct request", "ok": False, "ms": 120},
            {"stage": "browser", "label": "Headless browser", "ok": True, "ms": 3000},
        ],
    }
    store.save(payload, options_key="k1")
    store.record_outcomes(payload)

    stats = store.domain_stats()
    assert len(stats) == 1
    row = stats[0]
    assert row["domain"] == "example.com"
    assert row["tries"] == 2
    assert row["wins"] == 1
    assert row["best_stage"] == "browser"
    assert row["success_rate"] == 50
