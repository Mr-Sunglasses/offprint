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
from typing import ClassVar

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

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    article_id UNINDEXED, title, site, body, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS domain_outcomes (
    domain       TEXT NOT NULL,
    stage        TEXT NOT NULL,
    tries        INTEGER NOT NULL DEFAULT 0,
    wins         INTEGER NOT NULL DEFAULT 0,
    fails        INTEGER NOT NULL DEFAULT 0,
    total_ms     INTEGER NOT NULL DEFAULT 0,
    last_win_at  REAL,
    last_fail_at REAL,
    PRIMARY KEY (domain, stage)
);
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

    _STAGE_ALIASES: ClassVar[dict[str, str]] = {
        "Direct request": "http",
        "Crawler User-Agent": "bot",
        "Headless browser": "browser",
        "Text proxy": "proxy",
        "Reader proxy": "proxy",
        "Wayback Machine": "archive",
    }

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # Older rows recorded the human label; fold them onto stage keys.
            for label, key in self._STAGE_ALIASES.items():
                conn.execute(
                    "UPDATE OR IGNORE domain_outcomes SET stage = ? WHERE stage = ?", (key, label)
                )
                conn.execute("DELETE FROM domain_outcomes WHERE stage = ?", (label,))

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
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO articles_fts (article_id, title, site, body) VALUES (?, ?, ?, ?)",
                (
                    article_id,
                    article.get("title", ""),
                    article.get("site", ""),
                    _plain(article.get("markdown", ""))[:200_000],
                ),
            )
        return article_id

    # ------------------------------------------------------------------ #
    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search over stored articles (FTS5, porter stemming)."""
        needle = " ".join((query or "").split())
        if not needle:
            return []
        match = " ".join(
            f'"{token}"*' for token in needle.split() if token.isalnum() or "'" in token
        )
        if not match:
            return []
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT a.id, a.url, a.title, a.site, a.word_count, a.fetched_at, a.preview,
                           snippet(articles_fts, 3, '<mark>', '</mark>', '…', 24) AS snippet
                    FROM articles_fts f
                    JOIN articles a ON a.id = f.article_id
                    WHERE articles_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
            except sqlite3.Error:
                return []
        return [
            {
                "article_id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "site": row["site"],
                "word_count": row["word_count"],
                "fetched_at": row["fetched_at"],
                "paywalled_preview": bool(row["preview"]),
                "snippet": row["snippet"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    def record_outcomes(self, payload: dict) -> None:
        """Track which stage wins per domain, for the health table."""
        article = payload.get("article") or {}
        url = article.get("url", "")
        domain = _domain_of(url)
        if not domain:
            return
        winner_stage = payload.get("winner_stage") or ""
        attempts = payload.get("attempts") or []
        now = time.time()
        with self._connect() as conn:
            for attempt in attempts:
                stage = attempt.get("stage") or attempt.get("label") or "unknown"
                won = bool(winner_stage) and stage == winner_stage
                ok = bool(attempt.get("ok"))
                conn.execute(
                    """
                    INSERT INTO domain_outcomes (domain, stage, tries, wins, fails, total_ms,
                                                 last_win_at, last_fail_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(domain, stage) DO UPDATE SET
                        tries = tries + 1,
                        wins = wins + excluded.wins,
                        fails = fails + excluded.fails,
                        total_ms = total_ms + excluded.total_ms,
                        last_win_at = COALESCE(excluded.last_win_at, last_win_at),
                        last_fail_at = COALESCE(excluded.last_fail_at, last_fail_at)
                    """,
                    (
                        domain,
                        stage,
                        int(won),
                        0 if ok else 1,
                        int(attempt.get("ms") or 0),
                        now if won else None,
                        now if not ok else None,
                    ),
                )

    def domain_stats(self, limit: int = 40) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT domain, stage, tries, wins, fails, total_ms, last_win_at, last_fail_at
                FROM domain_outcomes ORDER BY domain, wins DESC
                """
            ).fetchall()

        by_domain: dict[str, dict] = {}
        for row in rows:
            entry = by_domain.setdefault(
                row["domain"],
                {
                    "domain": row["domain"],
                    "tries": 0,
                    "wins": 0,
                    "fails": 0,
                    "stages": [],
                    "last_win_at": None,
                },
            )
            entry["tries"] += row["tries"]
            entry["wins"] += row["wins"]
            entry["fails"] += row["fails"]
            entry["stages"].append(
                {
                    "stage": row["stage"],
                    "tries": row["tries"],
                    "wins": row["wins"],
                    "avg_ms": round(row["total_ms"] / max(row["tries"], 1)),
                }
            )
            if row["last_win_at"]:
                entry["last_win_at"] = max(entry["last_win_at"] or 0, row["last_win_at"])

        for entry in by_domain.values():
            entry["stages"].sort(key=lambda s: (-s["wins"], -s["tries"]))
            winner = next((s for s in entry["stages"] if s["wins"] > 0), None)
            entry["best_stage"] = winner["stage"] if winner else ""
            entry["success_rate"] = round(100 * entry["wins"] / max(entry["tries"], 1))

        ranked = sorted(by_domain.values(), key=lambda e: (-e["tries"], e["domain"]))
        return ranked[:limit]

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

    def reindex_missing(self) -> int:
        """Index articles saved before the FTS table existed (runs at startup)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, site, markdown FROM articles "
                "WHERE id NOT IN (SELECT article_id FROM articles_fts)"
            ).fetchall()
            for row in rows:
                conn.execute(
                    "INSERT INTO articles_fts (article_id, title, site, body) VALUES (?, ?, ?, ?)",
                    (row["id"], row["title"], row["site"], _plain(row["markdown"])[:200_000]),
                )
        return len(rows)

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


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


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
