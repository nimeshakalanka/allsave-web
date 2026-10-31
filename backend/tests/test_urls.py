import pytest

from app.errors import APIError
from app import urls
from app.urls import SafeRedirectHandler, validate_url


def public(_: str) -> list[str]:
    return ["142.250.80.46"]


def resolves_to(*addrs: str):
    return lambda _: list(addrs)


@pytest.mark.parametrize(("url", "platform"), [
    ("https://www.tiktok.com/@user/video/7123456789012345678", "tiktok"),
    ("https://vm.tiktok.com/ZMabc123/", "tiktok"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ("https://youtube.com/shorts/abcdefghijk", "youtube"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ("https://youtu.be/dQw4w9WgXcQ?si=xyz", "youtube"),
    ("https://www.instagram.com/reel/Cabc123/", "instagram"),
    ("https://instagram.com/p/Cabc123/", "instagram"),
    ("https://www.facebook.com/watch/?v=123", "facebook"),
    ("https://web.facebook.com/user/videos/123/", "facebook"),
    ("https://fb.watch/abc123/", "facebook"),
])
def test_accepts_allowlisted(url: str, platform: str) -> None:
    assert validate_url(url, public)[0] == platform


def test_normalizes_case_trailing_dot_and_fragment() -> None:
    assert validate_url("  HTTPS://WWW.YouTube.COM./watch?v=abc#t=10 ", public) == (
        "youtube", "https://www.youtube.com/watch?v=abc")


def test_schemeless_becomes_https() -> None:
    assert validate_url("youtu.be/abc", public) == ("youtube", "https://youtu.be/abc")


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=abc",
    "ftp://youtube.com/x",
    "javascript:alert(1)//youtube.com",
    "https://user:pass@www.youtube.com/watch?v=abc",
    "https://www.youtube.com@evil.com/watch?v=abc",
    "https://www.youtube.com:8443/watch?v=abc",
    "https://www.youtube.com:notaport/",
    "",
    "   ",
    "https://youtu.be/" + "a" * 2100,
])
def test_rejects_invalid(url: str) -> None:
    with pytest.raises(APIError) as e:
        validate_url(url, public)
    assert e.value.code in ("invalid_url", "unsupported_site")


@pytest.mark.parametrize("url", [
    "https://www.envato.com/item/1",
    "https://elements.envato.com/x",
    "https://www.pornhub.com/view_video.php?viewkey=1",
    "https://www.dailymotion.com/video/x1",
    "https://facebook.com.evil.com/watch?v=1",
    "https://youtube.com.evil.com/watch?v=abc",
    "https://evil-youtube.com/watch?v=abc",
    "https://notinstagram.com/p/x",
    "https://api.tiktok.com/x",
    "https://142.250.80.46/watch?v=abc",
    "https://[::1]/",
    "https://localhost/",
])
def test_rejects_unsupported_domains(url: str) -> None:
    with pytest.raises(APIError) as e:
        validate_url(url, public)
    assert e.value.code == "unsupported_site"
    assert e.value.status == 400


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1",
                                  "fd00::1", "0.0.0.0", "100.64.0.1"])
def test_rejects_private_resolution(addr: str) -> None:
    with pytest.raises(APIError) as e:
        validate_url("https://www.youtube.com/watch?v=abc", resolves_to(addr))
    assert e.value.code == "invalid_url"


def test_rejects_when_any_address_is_private() -> None:
    with pytest.raises(APIError):
        validate_url("https://www.tiktok.com/@a/video/1", resolves_to("142.250.80.46", "10.0.0.1"))


def test_rejects_unresolvable_host() -> None:
    def fail(_: str) -> list[str]:
        raise OSError("nxdomain")

    with pytest.raises(APIError) as e:
        validate_url("https://www.youtube.com/watch?v=abc", fail)
    assert e.value.code == "invalid_url"


@pytest.mark.parametrize("target", [
    "https://169.254.169.254/latest/meta-data/",
    "http://www.tiktok.com/@a/video/1",
    "https://evil.example/@a/video/1",
    "https://127.0.0.1:8000/admin",
])
def test_redirects_to_disallowed_targets_are_blocked(target: str) -> None:
    with pytest.raises(APIError):
        SafeRedirectHandler().redirect_request(None, None, 302, "Found", {}, target)


class FakeResponse:
    def __init__(self, url: str) -> None:
        self.url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.mark.parametrize(("final", "expected"), [
    ("https://www.facebook.com/reel/978314331995163/?fs=e", "https://www.facebook.com/reel/978314331995163/?fs=e"),
    ("https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Freel%2F123%2F", "https://www.facebook.com/reel/123/"),
])
def test_facebook_share_links_are_expanded(monkeypatch: pytest.MonkeyPatch, final: str, expected: str) -> None:
    monkeypatch.setattr(urls, "resolve_host", public)
    monkeypatch.setattr(urls.yt_dlp.YoutubeDL, "urlopen", lambda _, __: FakeResponse(final))
    assert urls.normalize("https://www.facebook.com/share/v/19ic1QBvm4/?mibextid=wwXIfr") == ("facebook", expected)


@pytest.mark.parametrize("final", ["https://evil.example/x", "https://www.facebook.com/login/"])
def test_facebook_share_rejects_bad_destinations(monkeypatch: pytest.MonkeyPatch, final: str) -> None:
    monkeypatch.setattr(urls, "resolve_host", public)
    monkeypatch.setattr(urls.yt_dlp.YoutubeDL, "urlopen", lambda _, __: FakeResponse(final))
    with pytest.raises(APIError):
        urls.normalize("https://fb.watch/abc/")
