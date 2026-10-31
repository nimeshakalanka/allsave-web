from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import media
from .activity import ActivityLog, Source
from .config import Settings
from .errors import APIError

log = logging.getLogger("allsave.jobs")
Status = Literal["queued", "downloading", "processing", "done", "error"]
_JOB_DIR = re.compile(r"[0-9a-f]{32}")


@dataclass
class Job:
    id: str
    url: str
    platform: str
    option_id: str
    dir: Path
    expires_at: float
    status: Status = "queued"
    progress: float = 0.0
    error: str | None = None
    file: Path | None = None
    filename: str | None = None
    created_at: float = field(default_factory=time.time)
    source: Source = "web"
    actor: int | str | None = None

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error")


class JobManager:
    """Owns all job state for one app instance. Each job lives in its own UUID directory under work_root."""

    def __init__(self, settings: Settings, activity: ActivityLog | None = None) -> None:
        self.s = settings
        self.activity = activity or ActivityLog(Settings())
        self.jobs: dict[str, Job] = {}
        self._sem = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        self.s.work_root.mkdir(parents=True, exist_ok=True)
        for p in self.s.work_root.iterdir():  # leftovers from a previous run (only our own UUID dirs)
            if p.is_dir() and _JOB_DIR.fullmatch(p.name):
                shutil.rmtree(p, ignore_errors=True)

    def create(self, url: str, platform: str, option_id: str, source: Source = "web",
               actor: int | str | None = None) -> Job:
        if sum(not j.finished for j in self.jobs.values()) >= self.s.max_queued_jobs:
            raise APIError(503, "busy", "The server is busy. Please try again in a minute.")
        job_id = uuid.uuid4().hex
        job = Job(id=job_id, url=url, platform=platform, option_id=option_id, dir=self.s.work_root / job_id,
                  expires_at=time.time() + self.s.job_ttl, source=source, actor=actor)
        job.dir.mkdir(parents=True)
        self.jobs[job_id] = job
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        return job if job and job.expires_at > time.time() else None

    def served(self, job_id: str) -> None:
        if job := self.jobs.get(job_id):
            job.expires_at = min(job.expires_at, time.time() + self.s.served_grace)

    async def _run(self, job: Job) -> None:
        def report(status: Status, progress: float) -> None:  # called from the worker thread
            job.status, job.progress = status, max(job.progress, progress)

        async with self._sem:
            job.status = "downloading"
            kind = "audio" if job.option_id == "audio" else "video"
            track = {"platform": job.platform, "kind": kind}
            try:
                path, title = await asyncio.to_thread(
                    media.download, job.url, job.platform, job.option_id, job.dir, self.s, report)
                job.file, job.filename = path, media.safe_filename(title, path.suffix.lstrip("."))
                job.status, job.progress = "done", 100.0
                self.activity.record(job.source, "download_ok", job.actor, **track)
            except APIError as e:
                job.status, job.error = "error", e.message
                self.activity.record(job.source, "download_failed", job.actor, code=e.code, **track)
            except Exception:
                log.exception("job %s failed", job.id)
                job.status, job.error = "error", "Something went wrong while downloading. Please try again."
                self.activity.record(job.source, "download_failed", job.actor, code="internal", **track)
        if job.status == "error":
            shutil.rmtree(job.dir, ignore_errors=True)

    def purge(self, everything: bool = False) -> None:
        now = time.time()
        for job in list(self.jobs.values()):
            # A running download can't be interrupted; it is purged on the first sweep after it finishes.
            if everything or (job.expires_at <= now and job.finished):
                shutil.rmtree(job.dir, ignore_errors=True)
                self.jobs.pop(job.id, None)

    async def sweep_forever(self, interval: float = 30) -> None:
        while True:
            await asyncio.sleep(interval)
            self.purge()
