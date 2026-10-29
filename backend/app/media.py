"""yt-dlp wrapper: info extraction, option building and downloads. All functions here are blocking."""
from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yt_dlp
from pydantic import BaseModel

from .config import Settings
from .errors import APIError, from_ytdlp

Info = dict[str, Any]
Report = Callable[[str, float], None]  # (status, progress 0..100)

# yt-dlp extractor names (lowercased, regex) we allow; everything else, including "generic", is disabled.
EXTRACTORS = ["youtube", "youtubeytbe", "tiktok", "vm.tiktok", "instagram", "instagramios"]
OPTION_ID = re.compile(r"audio|v\d{2,4}|f:[\w.\-]{1,80}")
AUDIO_KBPS = 192
MAX_VIDEO_OPTIONS = 6


class MediaOption(BaseModel):
    id: str
    kind: Literal["video", "audio"]
    label: str
    ext: str
    height: int | None
    watermark: bool | None
    size: int | None


class MediaInfo(BaseModel):
    platform: str
    title: str
    thumbnail: str | None
    duration: int | None
    options: list[MediaOption]


def _opts(s: Settings, workdir: Path, **extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "playlist_items": "1",
        "allowed_extractors": EXTRACTORS, "socket_timeout": 20, "retries": 3, "paths": {"temp": str(workdir)},
        **extra,
    }
    if s.proxy_url:
        opts["proxy"] = s.proxy_url
    if s.cookies_file:
        # yt-dlp writes cookies back on exit, so each run gets a private copy to avoid concurrent writes.
        dst = workdir / "cookies.txt"
        shutil.copyfile(s.cookies_file, dst)
        opts["cookiefile"] = str(dst)
    return opts


def _unwrap(info: Info | None) -> Info:
    if info and info.get("_type") == "playlist":
        info = next(iter(info.get("entries") or []), None)
    if not info or not info.get("formats"):
        raise APIError(404, "no_media", "No downloadable video was found at that link.")
    return info


def _check_limits(info: Info, s: Settings) -> None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise APIError(422, "live", "Live streams and premieres are not supported.")
    if (info.get("duration") or 0) > s.max_duration:
        raise APIError(422, "too_long", f"Videos longer than {s.max_duration // 60} minutes are not supported.")


def _size(f: Info) -> int | None:
    return f.get("filesize") or f.get("filesize_approx")


def _res(f: Info) -> int:
    """Quality label height: the short side, so vertical 1080x1920 reads as 1080p."""
    h, w = f.get("height") or 0, f.get("width") or 0
    return min(h, w) if h and w else h


def _best_per_height(formats: list[Info]) -> list[Info]:
    best: dict[int, Info] = {}
    for f in formats:  # yt-dlp sorts formats worst -> best, so later entries win
        best[f["height"]] = f
    return [best[h] for h in sorted(best, reverse=True)]


def _is_watermarked(f: Info) -> bool:
    return "watermark" in (f.get("format_note") or "").lower()


def build_options(info: Info, platform: str, max_bytes: int) -> list[MediaOption]:
    formats: list[Info] = info.get("formats") or []
    videos = [f for f in formats if f.get("vcodec") != "none" and f.get("height") and f.get("format_id")
              and "unplayable" not in (f.get("format_note") or "").lower()]
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    options: list[MediaOption] = []

    def add_video(oid: str, f: Info, label: str, size: int | None, watermark: bool | None, ext: str) -> None:
        options.append(MediaOption(id=oid, kind="video", label=label, ext=ext, height=_res(f), watermark=watermark,
                                   size=size))

    if platform == "tiktok":
        clean = [f for f in videos if not _is_watermarked(f)]
        marked = [f for f in videos if _is_watermarked(f)]
        for f in _best_per_height(clean)[:3]:
            add_video(f"f:{f['format_id']}", f, f"{_res(f)}p · No watermark", _size(f), False, f.get("ext") or "mp4")
        if marked:
            f = marked[-1]
            add_video(f"f:{f['format_id']}", f, f"{_res(f)}p · With watermark", _size(f), True, f.get("ext") or "mp4")
    else:
        audio_size = _size(audios[-1]) if audios else None
        for f in _best_per_height(videos)[:MAX_VIDEO_OPTIONS]:
            size = _size(f)
            if size and f.get("acodec") in (None, "none") and audio_size:
                size += audio_size
            add_video(f"v{f['height']}", f, f"{_res(f)}p", size, None, "mp4")

    if audios or videos:
        duration = info.get("duration")
        options.append(MediaOption(id="audio", kind="audio", label=f"MP3 · {AUDIO_KBPS} kbps", ext="mp3", height=None,
                                   watermark=None, size=int(duration * AUDIO_KBPS * 125) if duration else None))
    return [o for o in options if not (o.size and o.size > max_bytes)]


def _format_opts(option_id: str) -> dict[str, Any]:
    if option_id == "audio":
        return {"format": "bestaudio/best", "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(AUDIO_KBPS)}]}
    if option_id.startswith("v"):
        h = int(option_id[1:])
        return {"format": f"bv*[height<={h}]+ba/b[height<={h}]", "merge_output_format": "mp4",
                "format_sort": ["res", "vcodec:h264", "ext:mp4:m4a"]}
    return {"format": option_id[2:]}


def fetch_info(url: str, platform: str, s: Settings) -> MediaInfo:
    s.work_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=s.work_root) as tmp, \
                yt_dlp.YoutubeDL(_opts(s, Path(tmp), format="bv*+ba/b/ba")) as ydl:
            info = _unwrap(ydl.extract_info(url, download=False))
    except yt_dlp.utils.DownloadError as e:
        raise from_ytdlp(str(e)) from None
    _check_limits(info, s)
    options = build_options(info, platform, s.max_bytes)
    if not options:
        raise APIError(422, "too_large", f"Every available format is larger than {s.max_filesize_mb} MB.")
    duration = info.get("duration")
    title = (info.get("title") or info.get("description") or "Untitled").strip()
    return MediaInfo(platform=platform, title=title[:200], thumbnail=info.get("thumbnail"),
                     duration=int(duration) if duration else None, options=options)


def safe_filename(title: str, ext: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:80] or "download"
    return f"{name}.{ext}"


def download(url: str, platform: str, option_id: str, workdir: Path, s: Settings, report: Report) -> tuple[Path, str]:
    """Download into workdir; returns (file, title)."""
    state = {"parts": 1, "done": 0}

    def on_progress(d: dict[str, Any]) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            frac = (d.get("downloaded_bytes") or 0) / total if total else 0.0
            report("downloading", min(99.0, (state["done"] + frac) / state["parts"] * 100))
        elif d["status"] == "finished":
            state["done"] += 1
            if state["done"] >= state["parts"]:
                report("processing", 99.0)

    def on_postprocess(d: dict[str, Any]) -> None:
        if d["status"] == "started":
            report("processing", 99.0)

    opts = _opts(s, workdir, outtmpl=str(workdir / "media.%(ext)s"), max_filesize=s.max_bytes,
                 progress_hooks=[on_progress], postprocessor_hooks=[on_postprocess], **_format_opts(option_id))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _unwrap(ydl.extract_info(url, download=False))
            _check_limits(info, s)
            if option_id not in {o.id for o in build_options(info, platform, s.max_bytes)}:
                raise APIError(400, "invalid_option", "That option is not available for this video.")
            state["parts"] = len(info.get("requested_formats") or ()) or 1
            ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise from_ytdlp(str(e)) from None

    files = [p for p in workdir.glob("media.*") if p.suffix not in (".part", ".ytdl", ".temp")]
    if not files:  # yt-dlp silently skips formats above max_filesize
        raise APIError(422, "too_large", f"This file is larger than {s.max_filesize_mb} MB.")
    path = max(files, key=lambda p: p.stat().st_size)
    if path.stat().st_size > s.max_bytes:
        raise APIError(422, "too_large", f"This file is larger than {s.max_filesize_mb} MB.")
    return path, (info.get("title") or "download").strip()
