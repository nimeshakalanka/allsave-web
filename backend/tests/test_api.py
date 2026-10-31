from pathlib import Path
from typing import Any

import pytest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app import media, urls
from app.activity import ActivityLog
from app.config import Settings
from app.main import create_app
from app.media import build_options

YT_INFO: dict[str, Any] = {
    "title": "Test video", "thumbnail": "https://i.ytimg.com/vi/x/hq.jpg", "duration": 212,
    "formats": [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "filesize": 3_000_000},
        {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "width": 640, "filesize": 9_000_000},
        {"format_id": "136", "vcodec": "avc1", "acodec": "none", "height": 720, "width": 1280, "filesize": 40_000_000},
        {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080, "width": 1920, "filesize": 900_000_000},
        {"format_id": "sb0", "vcodec": "none", "acodec": "none", "ext": "mhtml"},
    ],
}


def tiktok_info(*, watermarked: bool) -> dict[str, Any]:
    formats = [
        {"format_id": "play_addr-0", "vcodec": "h264", "acodec": "aac", "height": 1024, "width": 576,
         "format_note": "Direct video", "filesize": 5_000_000},
        {"format_id": "bytevc2_720p-0", "vcodec": "bytevc2", "acodec": "aac", "height": 1280, "width": 720,
         "format_note": "Playback video (UNPLAYABLE)"},
        {"format_id": "h264_720p-0", "vcodec": "h264", "acodec": "aac", "height": 1280, "width": 720,
         "format_note": "Playback video", "filesize": 7_000_000},
    ]
    if watermarked:
        formats.insert(0, {"format_id": "download_addr-0", "vcodec": "h264", "acodec": "aac", "height": 1024,
                           "width": 576, "format_note": "Download video, watermarked", "filesize": 5_500_000})
    return {"title": "tt", "duration": 15, "formats": formats}


def test_youtube_options_grouped_and_size_filtered() -> None:
    opts = build_options(YT_INFO, "youtube", max_bytes=500 * 1024 * 1024)
    assert [o.id for o in opts] == ["v720", "v360", "audio"]  # 1080p (~900 MB) exceeds the limit
    v720 = opts[0]
    assert (v720.kind, v720.label, v720.ext, v720.height, v720.watermark) == ("video", "720p", "mp4", 720, None)
    assert v720.size == 43_000_000  # video-only + best audio
    assert opts[-1].kind == "audio" and opts[-1].ext == "mp3" and opts[-1].size == 212 * 192 * 125


def test_tiktok_offers_both_watermark_variants() -> None:
    opts = build_options(tiktok_info(watermarked=True), "tiktok", max_bytes=10**9)
    videos = [o for o in opts if o.kind == "video"]
    assert [(o.id, o.watermark, o.label) for o in videos] == [
        ("f:h264_720p-0", False, "720p · No watermark"),
        ("f:play_addr-0", False, "576p · No watermark"),
        ("f:download_addr-0", True, "576p · With watermark"),
    ]
    assert all("bytevc2" not in o.id for o in opts)


def test_facebook_without_heights_offers_best_quality() -> None:
    info = {"duration": 30, "formats": [{"format_id": "sd", "ext": "mp4"}, {"format_id": "hd", "ext": "mp4"}]}
    opts = build_options(info, "facebook", max_bytes=10**9)
    assert [(o.id, o.kind, o.label) for o in opts] == [("f:hd", "video", "Best quality"), ("audio", "audio", "MP3 · 192 kbps")]


def test_tiktok_clean_only_is_labelled() -> None:
    opts = build_options(tiktok_info(watermarked=False), "tiktok", max_bytes=10**9)
    assert all(o.watermark is False for o in opts if o.kind == "video")
    assert all("No watermark" in o.label for o in opts if o.kind == "video")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(urls, "resolve_host", lambda _: ["142.250.80.46"])
    settings = Settings(work_root=tmp_path, rate_limit_info="3/minute")
    with TestClient(create_app(settings, run_bot=False)) as c:
        yield c


def test_info_rejects_other_domains(client: TestClient) -> None:
    r = client.post("/api/info", json={"url": "https://elements.envato.com/some-item"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_site"


def test_info_returns_options(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, platform: str, s: Settings) -> media.MediaInfo:
        return media.MediaInfo(platform=platform, title=YT_INFO["title"], thumbnail=YT_INFO["thumbnail"],
                               duration=212, options=build_options(YT_INFO, platform, s.max_bytes))

    monkeypatch.setattr(media, "fetch_info", fake_fetch)
    r = client.post("/api/info", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 200
    body = r.json()
    assert body["platform"] == "youtube" and body["duration"] == 212
    assert {o["kind"] for o in body["options"]} == {"video", "audio"}
    assert set(body["options"][0]) == {"id", "kind", "label", "ext", "height", "watermark", "size"}


def test_info_rate_limit(client: TestClient) -> None:
    bad = {"url": "https://example.com/x"}
    codes = [client.post("/api/info", json=bad).status_code for _ in range(4)]
    assert codes == [400, 400, 400, 429]


def test_job_rejects_malformed_option(client: TestClient) -> None:
    r = client.post("/api/jobs", json={"url": "https://youtu.be/abc", "option_id": "f:$(rm -rf)"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_option"


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get(f"/api/jobs/{'a' * 32}").status_code == 404
    assert client.get("/api/jobs/../../etc/passwd").status_code in (404, 422)
    assert client.get("/api/jobs/not-a-uuid/file").status_code == 422


def test_job_lifecycle_and_cleanup(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_download(url, platform, option_id, workdir: Path, s, report):  # type: ignore[no-untyped-def]
        report("downloading", 50)
        out = workdir / "media.mp3"
        out.write_bytes(b"ID3fake")
        return out, "My: Song?"

    monkeypatch.setattr(media, "download", fake_download)
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/abc", "option_id": "audio"}).json()["job_id"]
    for _ in range(50):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] == "done":
            break
    assert status == {"status": "done", "progress": 100, "error": None}
    r = client.get(f"/api/jobs/{job_id}/file")
    assert r.status_code == 200 and r.content == b"ID3fake"
    assert "My%20Song.mp3" in r.headers["content-disposition"]

    manager = client.app.state.jobs  # type: ignore[attr-defined]
    manager.jobs[job_id].expires_at = 0
    manager.purge()
    assert not (tmp_path / job_id).exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(doc)


@pytest.fixture
def activity_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(urls, "resolve_host", lambda _: ["142.250.80.46"])
    collection = FakeCollection()

    async def fake_hash(_: ActivityLog, ip: str) -> str:
        return f"hashed-{len(ip)}"

    monkeypatch.setattr(ActivityLog, "_db", lambda _: {"activity": collection})
    monkeypatch.setattr(ActivityLog, "_hash", fake_hash)
    monkeypatch.setattr(ActivityLog, "init", AsyncMock())
    monkeypatch.setattr(ActivityLog, "close", AsyncMock())
    settings = Settings(work_root=tmp_path, mongo_uri="mongodb://fake")
    with TestClient(create_app(settings, run_bot=False)) as c:
        yield c, collection


def test_web_activity_is_recorded_without_raw_ip(activity_client) -> None:  # type: ignore[no-untyped-def]
    client, collection = activity_client
    assert client.post("/api/visit").status_code == 204
    client.post("/api/info", json={"url": "https://example.com/x"})
    client.get("/healthz")  # lets queued writes run
    events = {d["event"]: d for d in collection.docs}
    assert events["visit"]["source"] == "web"
    assert events["fetch_failed"]["code"] == "unsupported_site" and "url" not in events["fetch_failed"]
    assert all(str(d["actor"]).startswith("hashed-") for d in collection.docs)


def test_job_outcome_is_recorded(activity_client, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    client, collection = activity_client

    def fake_download(url, platform, option_id, workdir: Path, s, report):  # type: ignore[no-untyped-def]
        out = workdir / "media.mp3"
        out.write_bytes(b"x")
        return out, "Song"

    monkeypatch.setattr(media, "download", fake_download)
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/abc", "option_id": "audio"}).json()["job_id"]
    for _ in range(50):
        if client.get(f"/api/jobs/{job_id}").json()["status"] == "done":
            break
    client.get("/healthz")
    done = [d for d in collection.docs if d["event"] == "download_ok"]
    assert done and done[0]["platform"] == "youtube" and done[0]["kind"] == "audio"


def test_activity_is_noop_without_mongo() -> None:
    assert not ActivityLog(Settings()).enabled
    ActivityLog(Settings()).record("web", "visit", "1.2.3.4")  # must not need an event loop or a database
