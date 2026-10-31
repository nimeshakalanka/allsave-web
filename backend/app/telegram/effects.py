"""Telegram message effects. Pyrogram's layer has no support, so these go through the HTTP Bot API.

Effects are decoration: they only work in private chats and every failure is ignored.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

log = logging.getLogger("allsave.telegram")

FIRE = "5104841245755180586"
PARTY = "5046509860389126442"
HEART = "5159385139981059251"


def _post(token: str, chat_id: int, text: str, effect: str) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML", "message_effect_id": effect,
                       "link_preview_options": {"is_disabled": True}}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10):
        pass


async def send_effect(token: str, chat_id: int, text: str, effect: str) -> bool:
    try:
        await asyncio.to_thread(_post, token, chat_id, text, effect)
        return True
    except Exception as e:  # never log the exception text: the request URL contains the bot token
        log.warning("Message effect failed: %s", type(e).__name__)
        return False
