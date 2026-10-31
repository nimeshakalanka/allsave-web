import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pyrogram import errors

from app.telegram.bot import in_channel, send_welcome


def fake_client(**kwargs):  # type: ignore[no-untyped-def]
    return SimpleNamespace(start_text=lambda m: f"hi {m}", start_kb=lambda: [], cfg=SimpleNamespace(CHID=-100), **kwargs)


def test_welcome_falls_back_to_text_when_photo_fails() -> None:
    client = fake_client(send_photo=AsyncMock(side_effect=errors.WebpageCurlFailed()), send_message=AsyncMock())
    asyncio.run(send_welcome(client, 1, "me", "https://example.com/x.png"))
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[:2] == (1, "hi me")


def test_channel_check_errors_do_not_block_users() -> None:
    client = fake_client(get_chat_member=AsyncMock(side_effect=errors.PeerIdInvalid()))
    assert asyncio.run(in_channel(client, 1)) is True


def test_non_member_is_still_refused() -> None:
    client = fake_client(get_chat_member=AsyncMock(side_effect=errors.UserNotParticipant()))
    assert asyncio.run(in_channel(client, 1)) is False
