import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pyrogram import errors

from app.telegram import effects
from app.telegram.bot import activity_text, apply_setting, credit_referral, fmt_duration, fmt_size, in_channel, option_rows, parse_duration, progress_bar, send_welcome, start


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
    message = SimpleNamespace(from_user=SimpleNamespace(id=1), chat=SimpleNamespace(type="private"), command=["start"],
                              reply_text=AsyncMock())
    asyncio.run(start(SimpleNamespace(store=store), message))
    assert "blocked" in message.reply_text.await_args.args[0]


def test_activity_text_renders_within_telegram_limit() -> None:
    data = {"days": 7, "total": 40, "bot_users": 3, "web_users": 9,
            "counts": {"bot": {"new_user": 1, "fetch_ok": 5, "fetch_failed": 1, "download_ok": 4},
                       "web": {"visit": 20, "fetch_ok": 8, "download_ok": 6}},
            "platforms": {"facebook": 7, "youtube": 3}, "errors": {"rate_limited": 2},
            "top_users": [(123, 4)], "daily": [("11-01", 5), ("11-02", 5)]}
    text = activity_text(data)
    assert "Links sent: <b>6</b>" in text and "Visits: <b>20</b>" in text and "Facebook: <b>7</b>" in text
    assert len(text) < 4096


def test_parse_duration() -> None:
    assert parse_duration("12h") == timedelta(hours=12) and parse_duration("3D") == timedelta(days=3)
    assert parse_duration("0h") is None and parse_duration("soon") is None


def fake_settings_client():  # type: ignore[no-untyped-def]
    store = SimpleNamespace(get_settings=AsyncMock(return_value={"free_daily_limit": 2}), update_settings=AsyncMock())
    return SimpleNamespace(store=store, svc=SimpleNamespace(activity=SimpleNamespace(record=lambda *a, **k: None)))


def test_settings_are_clamped_and_validated() -> None:
    client = fake_settings_client()
    assert asyncio.run(apply_setting(client, "limit", ["0"])) is None
    client.store.update_settings.assert_awaited_with(free_daily_limit=1)
    assert asyncio.run(apply_setting(client, "refer", ["3"])) is not None  # missing days
    assert asyncio.run(apply_setting(client, "free", ["soon"])) is not None
    assert asyncio.run(apply_setting(client, "nope", [])) is not None


def referral_client(count: int):  # type: ignore[no-untyped-def]
    expiry = datetime(2027, 1, 1, tzinfo=timezone.utc)
    store = SimpleNamespace(add_referral=AsyncMock(return_value=count), make_premium=AsyncMock(return_value=expiry),
                            get_settings=AsyncMock(return_value={"refer_count": 3, "refer_days": 3}))
    return SimpleNamespace(store=store, send_message=AsyncMock(), cfg=SimpleNamespace(BOT_TOKEN="t"),
                           svc=SimpleNamespace(activity=SimpleNamespace(record=lambda *a, **k: None)))


def test_every_third_referral_earns_premium(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("app.telegram.bot.send_effect", AsyncMock(return_value=True))
    client = referral_client(2)
    asyncio.run(credit_referral(client, 10, 20))
    client.store.make_premium.assert_not_awaited()
    client = referral_client(3)
    asyncio.run(credit_referral(client, 11, 20))
    client.store.make_premium.assert_awaited_once_with(20, 3)


def test_rejected_referral_gives_nothing() -> None:
    client = referral_client(0)
    client.store.add_referral = AsyncMock(return_value=None)
    asyncio.run(credit_referral(client, 20, 20))
    client.store.make_premium.assert_not_awaited()
