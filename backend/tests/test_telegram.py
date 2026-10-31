import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pyrogram import errors

from app.telegram import effects
from app.telegram.bot import fmt_duration, fmt_size, in_channel, option_rows, progress_bar, send_welcome, start


def fake_client(**kwargs):  # type: ignore[no-untyped-def]
    return SimpleNamespace(start_text=lambda m: f"hi {m}", start_kb=lambda: [], cfg=SimpleNamespace(CHID=-100), **kwargs)


def test_welcome_falls_back_to_text_when_photo_fails() -> None:
    client = fake_client(send_photo=AsyncMock(side_effect=errors.WebpageCurlFailed()), send_message=AsyncMock())
    user = SimpleNamespace(id=1, mention=lambda style=None: "me")
    asyncio.run(send_welcome(client, user, "https://example.com/x.png"))
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[:2] == (1, "hi me")


def test_channel_check_errors_do_not_block_users() -> None:
    client = fake_client(get_chat_member=AsyncMock(side_effect=errors.PeerIdInvalid()))
    assert asyncio.run(in_channel(client, 1)) is True


def test_non_member_is_still_refused() -> None:
    client = fake_client(get_chat_member=AsyncMock(side_effect=errors.UserNotParticipant()))
    assert asyncio.run(in_channel(client, 1)) is False


def test_progress_bar_and_formats() -> None:
    assert progress_bar(0) == "▱" * 10
    assert progress_bar(50) == "▰" * 5 + "▱" * 5
    assert progress_bar(100) == "▰" * 10
    assert fmt_duration(65) == "1:05" and fmt_duration(3725) == "1:02:05"
    assert fmt_size(5 * 1048576) == "5 MB" and fmt_size(None) == ""


def test_option_rows_fit_telegram_limits() -> None:
    options = [{"kind": "video", "label": f"{h}p", "size": 1048576} for h in (1080, 720, 480)]
    options.append({"kind": "audio", "label": "MP3", "size": None})
    rows = option_rows(options, "a" * 12)
    assert [len(r) for r in rows] == [2, 1, 1, 1]
    assert rows[-2][0].callback_data == "dl:aaaaaaaaaaaa:3" and rows[-1][0].callback_data.startswith("cx:")
    assert all(len(b.callback_data.encode()) <= 64 for r in rows for b in r)


def test_effect_failure_never_raises_or_logs_the_token(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    def boom(*_, **__):  # type: ignore[no-untyped-def]
        raise OSError("https://api.telegram.org/botSECRET/sendMessage")

    monkeypatch.setattr(effects.urllib.request, "urlopen", boom)
    assert asyncio.run(effects.send_effect("SECRET", 1, "hi", effects.FIRE)) is False
    assert "SECRET" not in caplog.text


def test_banned_user_gets_refused_on_start() -> None:
    store = SimpleNamespace(is_banned=AsyncMock(return_value=True))
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), chat=SimpleNamespace(type="private"), reply_text=AsyncMock())
    asyncio.run(start(SimpleNamespace(store=store), message))
    assert "blocked" in message.reply_text.await_args.args[0]
