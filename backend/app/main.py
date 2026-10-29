from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.background import BackgroundTask

from . import media, urls
from .config import Settings
from .errors import APIError
from .jobs import JobManager, Status
from .service import AllSave

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("allsave")
JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


class InfoRequest(BaseModel):
    url: str = Field(max_length=urls.MAX_URL_LEN)


class JobRequest(InfoRequest):
    option_id: str = Field(max_length=100)


class JobCreated(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: Status
    progress: int
    error: str | None


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(settings: Settings | None = None, *, run_bot: bool = True) -> FastAPI:
    s = settings or Settings.from_env()
    jobs = JobManager(s)
    svc = AllSave(s, jobs)
    limiter = Limiter(key_func=get_remote_address)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.start()
        sweeper = asyncio.create_task(jobs.sweep_forever())
        bot = None
        if run_bot:
            from .telegram import start_bot  # pyrogram/pymongo are only imported when the bot is configured
            try:
                bot = await start_bot(svc)
            except Exception:
                log.exception("Telegram bot failed to start; the web API keeps running")
        yield
        if bot:
            await bot.shutdown()
        sweeper.cancel()
        jobs.purge(everything=True)

    app = FastAPI(title="AllSave API", lifespan=lifespan)
    app.state.limiter = limiter
    app.state.jobs = jobs
    app.add_middleware(CORSMiddleware, allow_origins=s.allowed_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"], expose_headers=["Content-Disposition"])

    @app.exception_handler(APIError)
    async def api_error(_: Request, e: APIError) -> JSONResponse:
        return _error(e.status, e.code, e.message)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(_: Request, __: RateLimitExceeded) -> JSONResponse:
        return _error(429, "too_many_requests", "Too many requests. Please slow down and try again shortly.")

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(422, "bad_request", "Invalid request.")

    @app.post("/api/info", response_model=media.MediaInfo)
    @limiter.limit(s.rate_limit_info)
    async def info(request: Request, body: InfoRequest) -> media.MediaInfo:
        return await svc.info(body.url)

    @app.post("/api/jobs", response_model=JobCreated)
    @limiter.limit(s.rate_limit_jobs)
    async def create_job(request: Request, body: JobRequest) -> JobCreated:
        return JobCreated(job_id=(await svc.create_job(body.url, body.option_id)).id)

    @app.get("/api/jobs/{job_id}", response_model=JobStatus)
    @limiter.limit("240/minute")
    async def job_status(request: Request, job_id: JobId) -> JobStatus:
        job = svc.job(job_id)
        return JobStatus(status=job.status, progress=int(job.progress), error=job.error)

    @app.get("/api/jobs/{job_id}/file")
    @limiter.limit("30/minute")
    async def job_file(request: Request, job_id: JobId) -> FileResponse:
        job = svc.job(job_id)
        if job.status != "done" or not job.file or not job.file.exists():
            raise APIError(409, "not_ready", "The file is not ready yet.")
        media_type = mimetypes.guess_type(job.file.name)[0] or "application/octet-stream"
        return FileResponse(job.file, media_type=media_type, filename=job.filename,
                            background=BackgroundTask(jobs.served, job.id))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
