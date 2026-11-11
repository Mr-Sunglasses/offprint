"""SQLite persistence for fetched articles.

In-memory jobs are transient by design (they carry a progress trace), but the
articles themselves are worth keeping: a permalink has to survive a restart, and
a URL you fetched an hour ago shouldn't be re-scraped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DEFAULT_DB = Path.home() / ".cache" / "bpc-fetch-web" / "articles.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    options_key   TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    site          TEXT NOT NULL DEFAULT '',
    author        TEXT NOT NULL DEFAULT '',
    published     TEXT NOT NULL DEFAULT '',
    word_count    INTEGER NOT NULL DEFAULT 0,
    markdown      TEXT NOT NULL,
    via           TEXT NOT NULL DEFAULT '',
    strategy      TEXT NOT NULL DEFAULT '{}',
    attempts      TEXT NOT NULL DEFAULT '[]',
    confidence    TEXT NOT NULL DEFAULT 'high',
    preview       INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT NOT NULL DEFAULT '',
    elapsed_ms    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_created ON articles(created_at DESC);
"""


class ArticleStore:
    def __init__(self, path: Path | str | None = None) -> None:
        # An explicit path wins; otherwise BPC_DB_PATH, otherwise the cache dir.
        self.path = Path(path) if path is not None else Path(os.getenv("BPC_DB_PATH", DEFAULT_DB))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ #
    def save(self, payload: dict, options_key: str = "") -> str:
        """Persist a fetch result payload; returns the article id."""
        article = payload["article"]
        article_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO articles (
                    id, url, options_key, title, site, author, published, word_count,
                    markdown, via, strategy, attempts, confidence, preview,
                    fetched_at, elapsed_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    article.get("url", ""),
                    options_key,
                    article.get("title", ""),
                    article.get("site", ""),
                    article.get("author", ""),
                    article.get("date", ""),
                    int(article.get("word_count", 0) or 0),
                    article.get("markdown", ""),
                    payload.get("via", ""),
                    json.dumps(payload.get("strategy") or {}),
                    json.dumps(payload.get("attempts") or []),
                    payload.get("confidence", "high"),
                    int(bool(payload.get("paywalled_preview"))),
                    payload.get("fetched_at", ""),
                    int(payload.get("elapsed_ms", 0) or 0),
                    time.time(),
                ),
            )
        return article_id

    # ------------------------------------------------------------------ #
    def get(self, article_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return self._row_to_payload(row) if row else None

    def find_by_url(self, url: str, options_key: str) -> dict | None:
        """Reuse a previous fetch of the same URL with the same options."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE url = ? AND options_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (url, options_key),
            ).fetchone()
        return self._row_to_payload(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, url, title, site, word_count, via, fetched_at, preview, created_at "
                "FROM articles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "article_id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "site": row["site"],
                "word_count": row["word_count"],
                "via": row["via"],
                "fetched_at": row["fetched_at"],
                "paywalled_preview": bool(row["preview"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_payload(row: sqlite3.Row) -> dict:
        return {
            "article_id": row["id"],
            "article": {
                "url": row["url"],
                "title": row["title"],
                "site": row["site"],
                "author": row["author"],
                "date": row["published"],
                "word_count": row["word_count"],
                "markdown": row["markdown"],
                "text": _plain(row["markdown"]),
                "reading_minutes": max(1, round((row["word_count"] or 0) / 220)),
                "excerpt": _plain(row["markdown"])[:280],
                "images": [],
            },
            "via": row["via"],
            "strategy": json.loads(row["strategy"] or "{}"),
            "attempts": json.loads(row["attempts"] or "[]"),
            "confidence": row["confidence"],
            "paywalled_preview": bool(row["preview"]),
            "fetched_at": row["fetched_at"],
            "elapsed_ms": row["elapsed_ms"],
        }


def _plain(markdown: str) -> str:
    import re

    plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)
    return re.sub(r"[*_`>#\[\]]", "", plain)


_store: ArticleStore | None = None


def get_store() -> ArticleStore:
    global _store
    if _store is None:
        _store = ArticleStore()
    return _store
