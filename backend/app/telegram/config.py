from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class TelegramConfig:
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    MONGO_URI: str
    SUDO: list[int] = field(default_factory=list)
    # Channel users must join before /start works (0 disables the check).
    CHID: int = 0
    CHANNEL_LINK: str = "https://t.me/szteambots"
    BOT_USERNAME: str = "AIl_Save_Bot"
    DEV_LINK: str = "https://t.me/akalankanime2"
    FREE_DAILY_LIMIT: int = 2
    COOLDOWN_MINUTES: int = 30
    PREMIUM_DAYS: int = 30
    # Existing collections from the original bot are kept so current users and premium subs carry over.
    USERS_DB: str = "main"
    SUBS_DB: str = "Cluster0"
    SUBS_COLLECTION: str = "sub1"
    # Pyrogram session file lives here; keep it on a volume so broadcasts can reach users after restarts.
    DATA_DIR: Path = Path("data")

    @classmethod
    def from_env(cls) -> TelegramConfig | None:
        """None when BOT_TOKEN is unset (bot disabled). Raises if the bot is enabled but misconfigured."""
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            return None
        missing = [n for n in ("API_ID", "API_HASH", "MONGO_URI") if not os.getenv(n, "").strip()]
        if missing:
            raise RuntimeError(f"BOT_TOKEN is set but {', '.join(missing)} is missing")
        env = os.getenv
        return cls(
            API_ID=int(env("API_ID", "")), API_HASH=env("API_HASH", "").strip(), BOT_TOKEN=token,
            MONGO_URI=env("MONGO_URI", "").strip(),
            SUDO=[int(x) for x in env("SUDO", "").replace(" ", "").split(",") if x],
            CHID=_int("CHID", 0),
            CHANNEL_LINK=env("CHANNEL_LINK", cls.CHANNEL_LINK), BOT_USERNAME=env("BOT_USERNAME", cls.BOT_USERNAME),
            DEV_LINK=env("DEV_LINK", cls.DEV_LINK),
            FREE_DAILY_LIMIT=_int("FREE_DAILY_LIMIT", cls.FREE_DAILY_LIMIT),
            COOLDOWN_MINUTES=_int("COOLDOWN_MINUTES", cls.COOLDOWN_MINUTES),
            PREMIUM_DAYS=_int("PREMIUM_DAYS", cls.PREMIUM_DAYS),
            USERS_DB=env("USERS_DB", cls.USERS_DB), SUBS_DB=env("SUBS_DB", cls.SUBS_DB),
            SUBS_COLLECTION=env("SUBS_COLLECTION", cls.SUBS_COLLECTION),
            DATA_DIR=Path(env("TELEGRAM_DATA_DIR", "") or cls.DATA_DIR),
        )
