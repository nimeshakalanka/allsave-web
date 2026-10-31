from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _opt(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


@dataclass(frozen=True)
class Settings:
    allowed_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    max_duration: int = 1800  # seconds
    max_filesize_mb: int = 500
    max_concurrent_jobs: int = 3
    max_queued_jobs: int = 20
    job_ttl: int = 900  # seconds; files and job records are purged after this
    served_grace: int = 60  # seconds a file stays available after it was streamed once
    cookies_file: str | None = None
    proxy_url: str | None = None
    mongo_uri: str | None = None  # enables the permanent activity log
    activity_db: str = "main"
    rate_limit_info: str = "20/minute"
    rate_limit_jobs: str = "10/minute"
    work_root: Path = Path(tempfile.gettempdir()) / "allsave-jobs"

    @property
    def max_bytes(self) -> int:
        return self.max_filesize_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
        work_dir = _opt("WORK_DIR")
        return cls(
            allowed_origins=origins,
            max_duration=_int("MAX_DURATION", cls.max_duration),
            max_filesize_mb=_int("MAX_FILESIZE_MB", cls.max_filesize_mb),
            max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", cls.max_concurrent_jobs),
            max_queued_jobs=_int("MAX_QUEUED_JOBS", cls.max_queued_jobs),
            job_ttl=_int("JOB_TTL", cls.job_ttl),
            cookies_file=_opt("YTDLP_COOKIES_FILE"),
            proxy_url=_opt("PROXY_URL"),
            mongo_uri=_opt("MONGO_URI"),
            activity_db=os.getenv("USERS_DB", cls.activity_db),
            rate_limit_info=os.getenv("RATE_LIMIT_INFO", cls.rate_limit_info),
            rate_limit_jobs=os.getenv("RATE_LIMIT_JOBS", cls.rate_limit_jobs),
            work_root=Path(work_dir) if work_dir else cls.work_root,
        )
