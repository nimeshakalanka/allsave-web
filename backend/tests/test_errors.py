import pytest

from app.errors import from_ytdlp


@pytest.mark.parametrize(("message", "code"), [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "private"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "removed"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "geo_blocked"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser", "rate_limited"),
    ("ERROR: [TikTok] 123: HTTP Error 429: Too Many Requests", "rate_limited"),
    ("ERROR: [Instagram] x: Requested content is not available, rate-limit reached or login required", "login_required"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate", "login_required"),
    ("ERROR: \x1b[0;31m[TikTok] 1: Video not available, status code 10204\x1b[0m", "removed"),
    ("ERROR: Requested format is not available. Use --list-formats", "invalid_option"),
    ("ERROR: You have requested merging of multiple formats but ffmpeg is not installed", "server_error"),
    ("ERROR: [TikTok] 1: Unexpected response from webpage request", "extract_failed"),
])
def test_ytdlp_error_mapping(message: str, code: str) -> None:
    assert from_ytdlp(message).code == code
