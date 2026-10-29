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
    me = await bot.get_me()
    log.info("Telegram bot @%s is running", me.username)
    return bot
