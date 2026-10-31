"""URL validation: https only, strict host allowlist, public IPs only, safe redirect resolution."""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit, urlunsplit

import yt_dlp
import yt_dlp.networking.exceptions

from .errors import APIError

ALLOWED_HOSTS: dict[str, str] = {
    **dict.fromkeys(("tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"), "tiktok"),
    **dict.fromkeys(("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"), "youtube"),
    **dict.fromkeys(("instagram.com", "www.instagram.com"), "instagram"),
    **dict.fromkeys(("facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch"), "facebook"),
}
# Short-link hosts that only redirect; we follow them ourselves so every hop is re-validated.
SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com", "fb.watch"}
MAX_URL_LEN = 2048
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _invalid(message: str) -> APIError:
    return APIError(400, "invalid_url", message)


def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list({str(info[4][0]).split("%", 1)[0] for info in infos})


def ensure_public(host: str, resolve: Callable[[str], list[str]] | None = None) -> None:
    try:
        addrs = (resolve or resolve_host)(host)
    except OSError:
        raise _invalid("Could not resolve that link's host.") from None
    if not addrs or not all(ipaddress.ip_address(a).is_global for a in addrs):
        raise _invalid("That link points to a private or reserved address.")


def validate_url(raw: str, resolve: Callable[[str], list[str]] | None = None) -> tuple[str, str]:
    """Return (platform, normalized_url) or raise APIError."""
    text = (raw or "").strip()
    if not text:
        raise _invalid("Paste a link first.")
    if len(text) > MAX_URL_LEN:
        raise _invalid("That link is too long.")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() != "https":
        raise _invalid("Only https:// links are supported.")
    if parts.username is not None or parts.password is not None:
        raise _invalid("Links with embedded credentials are not allowed.")
    try:
        port = parts.port
    except ValueError:
        raise _invalid("That link has an invalid port.") from None
    if port not in (None, 443):
        raise _invalid("Links with custom ports are not allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    platform = ALLOWED_HOSTS.get(host)
    if platform is None:
        raise APIError(400, "unsupported_site", "Only TikTok, YouTube, Instagram and Facebook links are supported.")
    ensure_public(host, resolve)
    return platform, urlunsplit(("https", host, parts.path or "/", parts.query, ""))


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_url(newurl)  # raises for non-allowlisted or internal targets
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def follow_short_link(url: str, proxy_url: str | None = None) -> str:
    handlers: list[urllib.request.BaseHandler] = [SafeRedirectHandler()]
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with opener.open(request, timeout=10) as resp:
            return resp.geturl()
    except urllib.error.HTTPError as e:  # final page may 4xx; the redirect chain is what we need
        return e.geturl() or url
    except (urllib.error.URLError, OSError):
        raise _invalid("Could not open that short link.") from None


def resolve_facebook_link(url: str, proxy_url: str | None = None) -> str:
    """Expand fb.watch and /share/ links with yt-dlp's networking; Facebook doesn't redirect plain clients.

    yt-dlp follows the hops itself, so only the final URL is validated (by the caller). The start host is allowlisted.
    """
    opts: dict[str, object] = {"quiet": True, "no_warnings": True, "socket_timeout": 15}
    if proxy_url:
        opts["proxy"] = proxy_url
    try:
        with yt_dlp.YoutubeDL(opts) as ydl, ydl.urlopen(url) as resp:
            final = resp.url
    except (yt_dlp.utils.DownloadError, yt_dlp.networking.exceptions.RequestError, OSError):
        raise _invalid("Could not open that Facebook link. Check it and try again.") from None
    parts = urlsplit(final)
    if parts.path.startswith("/login"):  # logged-out visitors land here with the real link in ?next=
        target = parse_qs(parts.query).get("next", [""])[0]
        if not target or urlsplit(target).path.startswith(("/share/", "/login")):
            raise APIError(403, "login_required", "Facebook wants a login for this link. Open the video and copy its full link.")
        return target
    return final


def normalize(raw: str, proxy_url: str | None = None) -> tuple[str, str]:
    """Validate, expand short links (re-validating every hop) and return (platform, url). Blocking."""
    platform, url = validate_url(raw)
    parts = urlsplit(url)
    if platform == "facebook" and (parts.hostname in SHORT_HOSTS or parts.path.startswith("/share/")):
        platform, url = validate_url(resolve_facebook_link(url, proxy_url))
    elif parts.hostname in SHORT_HOSTS:
        platform, url = validate_url(follow_short_link(url, proxy_url))
    return platform, url
