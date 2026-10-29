from __future__ import annotations

import re


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Checked in order; the first matching pattern wins.
_RULES: list[tuple[re.Pattern[str], int, str, str]] = [
    (re.compile(r"ffmpeg.*not (installed|found)|ffprobe.*not found"), 500, "server_error",
     "The server is missing ffmpeg. Please contact the site owner."),
    (re.compile(r"requested format.*not available"), 400, "invalid_option",
     "That quality is no longer available. Fetch the link again and pick another option."),
    (re.compile(r"login required|sign in to confirm your age|age.restricted|members.only|join this channel"), 403,
     "login_required", "This video needs a logged-in account (age-restricted, members-only or login-walled)."),
    (re.compile(r"\b429\b|too many requests|rate.?limit|confirm you.re not a bot"), 429, "rate_limited",
     "The platform is rate-limiting us right now. Please try again in a few minutes."),
    (re.compile(r"private"), 403, "private", "This video is private."),
    (re.compile(r"in your (country|region|location)|geo.?restrict|geo.?block"),
     451, "geo_blocked", "This video is not available in the server's region."),
    (re.compile(r"unavailable|not available|removed|does not exist|deleted|terminated|\b404\b|not found"), 404,
     "removed", "This video was removed or is unavailable."),
    (re.compile(r"unsupported url|no suitable extractor"), 400, "unsupported_site",
     "This link is not a supported TikTok, YouTube or Instagram video."),
]


def from_ytdlp(message: str) -> APIError:
    text = _ANSI.sub("", message).lower()
    for pattern, status, code, friendly in _RULES:
        if pattern.search(text):
            return APIError(status, code, friendly)
    return APIError(502, "extract_failed", "Could not fetch this video. Check the link and try again.")
