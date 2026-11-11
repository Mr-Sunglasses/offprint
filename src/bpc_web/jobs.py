"""In-memory job store. A fetch takes seconds, so polling beats WebSockets here."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .browser import BrowserPool
from .fetcher import FetchError, FetchOptions, FetchResult, fetch_article
from .store import get_store

MAX_JOBS_KEPT = 300
CACHE_TTL_SECONDS = 900


@dataclass
class Job:
    id: str
    url: str
    options: FetchOptions
    status: str = "queued"  # queued | running | done | error
    steps: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: dict | None = None
    created_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "status": self.status,
            "steps": self.steps,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobStore:
    def __init__(self, pool: BrowserPool, max_concurrent: int = 4) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._cache: dict[str, tuple[float, dict]] = {}
        self._pool = pool
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ #
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cached(self, key: str) -> dict | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if time.time() - ts > CACHE_TTL_SECONDS:
            self._cache.pop(key, None)
            return None
        return payload

    # ------------------------------------------------------------------ #
    def submit(self, url: str, options: FetchOptions, cache_key: str) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            url=url,
            options=options,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._jobs[job.id] = job
        self._order.append(job.id)
        self._prune()

        task = asyncio.create_task(self._run(job, cache_key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    # ------------------------------------------------------------------ #
    async def _run(self, job: Job, cache_key: str) -> None:
        job.status = "running"
        store = get_store()

        def progress(stage: str, status: str, label: str) -> None:
            job.steps.append(
                {
                    "stage": stage,
                    "status": status,
                    "label": label,
                    "at": round(time.time() * 1000),
                }
            )

        try:
            cached = self.cached(cache_key) or store.find_by_url(job.url, cache_key)
            if cached is not None:
                progress("cache", "done", "Served from cache")
                job.result = {**cached, "cached": True}
                job.status = "done"
                job.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
                return

            async with self._sem:
                result: FetchResult = await fetch_article(
                    job.url, job.options, pool=self._pool, progress=progress
                )

            payload = result.to_dict()
            payload["article_id"] = store.save(payload, options_key=cache_key)
            self._cache[cache_key] = (time.time(), payload)
            job.result = payload
            job.status = "done"
        except FetchError as exc:
            job.status = "error"
            job.error = {"code": exc.code, "message": str(exc)}
        except asyncio.CancelledError:
            job.status = "error"
            job.error = {"code": "cancelled", "message": "Fetch was cancelled."}
            raise
        except Exception as exc:
            job.status = "error"
            job.error = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            job.finished_at = job.finished_at or datetime.now(UTC).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ #
    def _prune(self) -> None:
        while len(self._order) > MAX_JOBS_KEPT:
            old = self._order.pop(0)
            self._jobs.pop(old, None)


class RateLimiter:
    """Plenty for a self-hosted deployment, no Redis required."""

    def __init__(self, limit: int = 40, window_seconds: int = 600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            retry_after = int(self.window - (now - hits[0])) + 1
            self._hits[key] = hits
            return False, retry_after
        hits.append(now)
        self._hits[key] = hits
        return True, 0
