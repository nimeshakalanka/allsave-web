"""Optional Telegram bot running inside the backend process. Enabled when BOT_TOKEN is set."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .config import TelegramConfig

if TYPE_CHECKING:
    from ..service import AllSave
    from .bot import AllSaveBot

log = logging.getLogger("allsave.telegram")


async def start_bot(svc: AllSave) -> AllSaveBot | None:
    cfg = TelegramConfig.from_env()
    if cfg is None:
        log.info("BOT_TOKEN not set; Telegram bot disabled")
        return None
    from .bot import AllSaveBot

    bot = AllSaveBot(cfg, svc)
    await bot.store.init()
    await bot.start()
    try:  # the "/" menu in Telegram; cosmetic, so a failure must not stop the bot
        from pyrogram.types import BotCommand
        await bot.set_bot_commands([
            BotCommand("start", "Main menu"), BotCommand("me", "My plan and usage"),
            BotCommand("history", "My recent downloads"), BotCommand("premium", "Unlimited downloads"),
            BotCommand("refer", "Invite friends, get free premium"),
            BotCommand("help", "How to use the bot")])
    except Exception:
        log.warning("Could not set the bot command menu")
    me = await bot.get_me()
    log.info("Telegram bot @%s is running", me.username)
    return bot
