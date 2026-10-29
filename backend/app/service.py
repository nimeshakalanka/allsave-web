"""Shared entry points used by both the HTTP API and the in-process Telegram bot."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from . import media, urls
from .config import Settings
from .errors import APIError
from .jobs import Job, JobManager


class AllSave:
    def __init__(self, settings: Settings, jobs: JobManager) -> None:
        self.s, self.jobs = settings, jobs
        self._info_sem = asyncio.Semaphore(settings.max_concurrent_jobs * 2)

    async def normalize(self, raw: str) -> tuple[str, str]:
        return await asyncio.to_thread(urls.normalize, raw, self.s.proxy_url)

    async def info(self, raw: str) -> media.MediaInfo:
        platform, url = await self.normalize(raw)
        async with self._info_sem:
            return await asyncio.to_thread(media.fetch_info, url, platform, self.s)

    async def create_job(self, raw: str, option_id: str) -> Job:
        if not media.OPTION_ID.fullmatch(option_id):
            raise APIError(400, "invalid_option", "Unknown download option.")
        platform, url = await self.normalize(raw)
        return self.jobs.create(url, platform, option_id)

    def job(self, job_id: str) -> Job:
        if not (job := self.jobs.get(job_id)):
            raise APIError(404, "not_found", "This download expired or does not exist. Start again.")
        return job

    async def wait(self, job_id: str, on_update: Callable[[str, int], Awaitable[None]], timeout: float = 900) -> Job:
        """Wait for a job to finish, reporting progress; returns the done job or raises APIError."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.job(job_id)
            if job.status == "done" and job.file:
                return job
            if job.status == "error":
                raise APIError(500, "job_failed", job.error or "Download failed.")
            await on_update(job.status, int(job.progress))
            await asyncio.sleep(2)
        raise APIError(504, "timeout", "The download took too long and was cancelled.")
