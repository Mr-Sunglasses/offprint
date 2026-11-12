"""Batch fetching: many URLs, one batch id, per-item status.

Batches reuse the single-fetch job machinery, so caching, concurrency limits and
the rate limiter behave exactly as they do for one link.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .fetcher import FetchOptions, normalise_url
from .jobs import JobStore, cache_key_for

MAX_BATCH_URLS = 50


class BatchError(Exception):
    pass


@dataclass
class Batch:
    id: str
    urls: list[str]
    job_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "urls": self.urls,
            "job_ids": self.job_ids,
        }


class BatchStore:
    def __init__(self, jobs: JobStore, max_kept: int = 40) -> None:
        self._jobs = jobs
        self._batches: dict[str, Batch] = {}
        self._order: list[str] = []
        self._max_kept = max_kept

    # ------------------------------------------------------------------ #
    def submit(self, raw_urls: list[str], options: FetchOptions) -> dict:
        cleaned: list[str] = []
        errors: list[dict] = []
        seen: set[str] = set()

        for raw in raw_urls:
            candidate = (raw or "").strip()
            if not candidate or candidate.startswith("#"):
                continue
            try:
                url = normalise_url(candidate)
            except Exception as exc:
                errors.append({"url": candidate, "error": str(exc)})
                continue
            if url in seen:
                continue
            seen.add(url)
            cleaned.append(url)

        if not cleaned:
            raise BatchError("No valid URLs in that batch.")
        if len(cleaned) > MAX_BATCH_URLS:
            raise BatchError(
                f"That's {len(cleaned)} links — the limit is {MAX_BATCH_URLS} per batch."
            )

        batch = Batch(
            id=uuid.uuid4().hex[:10],
            urls=cleaned,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        for url in cleaned:
            job = self._jobs.submit(url, options, cache_key_for(url, options))
            batch.job_ids.append(job.id)

        self._batches[batch.id] = batch
        self._order.append(batch.id)
        while len(self._order) > self._max_kept:
            self._batches.pop(self._order.pop(0), None)

        return {**batch.to_dict(), "skipped": errors, "count": len(cleaned)}

    # ------------------------------------------------------------------ #
    def get(self, batch_id: str) -> dict | None:
        batch = self._batches.get(batch_id)
        if batch is None:
            return None

        items = []
        done = failed = 0
        for url, job_id in zip(batch.urls, batch.job_ids, strict=False):
            job = self._jobs.get(job_id)
            if job is None:
                items.append({"url": url, "job_id": job_id, "status": "unknown"})
                continue
            result = job.result or {}
            article = result.get("article") or {}
            if job.status == "done":
                done += 1
            elif job.status == "error":
                failed += 1
            items.append(
                {
                    "url": url,
                    "job_id": job_id,
                    "status": job.status,
                    "article_id": result.get("article_id"),
                    "title": article.get("title", ""),
                    "site": article.get("site", ""),
                    "words": article.get("word_count", 0),
                    "via": result.get("via", ""),
                    "confidence": result.get("confidence", ""),
                    "preview": bool(result.get("paywalled_preview")),
                    "cached": bool(result.get("cached")),
                    "error": (job.error or {}).get("message", ""),
                    "steps": job.steps[-1]["label"] if job.steps else "",
                }
            )

        total = len(items)
        status = "done" if done + failed == total else "running"
        return {
            "id": batch.id,
            "created_at": batch.created_at,
            "status": status,
            "total": total,
            "done": done,
            "failed": failed,
            "items": items,
        }
