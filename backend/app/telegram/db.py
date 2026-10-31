"""MongoDB persistence: users/groups, premium subscriptions, download limits and pending link selections."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from .config import TelegramConfig

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
WINDOW = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_wait(delta: timedelta) -> str:
    minutes = max(1, int(delta.total_seconds() // 60) + 1)
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes} min"


class Store:
    def __init__(self, cfg: TelegramConfig) -> None:
        self.cfg = cfg
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(cfg.MONGO_URI, tz_aware=True)
        main = self.client[cfg.USERS_DB]
        self.users = main["users"]
        self.groups = main["groups"]
        self.limits = main["limits"]
        self.pending = main["pending"]
        self.downloads = main["downloads"]
        self.bans = main["bans"]
        self.subs = self.client[cfg.SUBS_DB][cfg.SUBS_COLLECTION]

    async def init(self) -> None:
        await self.pending.create_index("created_at", expireAfterSeconds=3600)
        await self.subs.create_index("user_id")
        await self.downloads.create_index([("user_id", 1), ("at", -1)])
        await self.downloads.create_index("at")

    async def close(self) -> None:
        await self.client.close()

    # users / groups (same documents as the original bot: ids stored as strings)
    async def add_user(self, user_id: int) -> bool:
        """Returns True the first time this user is seen."""
        result = await self.users.update_one({"user_id": str(user_id)}, {"$setOnInsert": {"user_id": str(user_id)}},
                                             upsert=True)
        return result.upserted_id is not None

    async def remove_user(self, user_id: int | str) -> None:
        await self.users.delete_one({"user_id": str(user_id)})

    async def add_group(self, chat_id: int) -> None:
        await self.groups.update_one({"chat_id": str(chat_id)}, {"$setOnInsert": {"chat_id": str(chat_id)}}, upsert=True)

    async def counts(self) -> tuple[int, int, int]:
        premium = len(await self.subs.distinct("user_id", {"expiry_date": {"$gt": _now()}}))
        return await self.users.count_documents({}), await self.groups.count_documents({}), premium

    def all_user_ids(self):  # type: ignore[no-untyped-def]
        return self.users.find({}, {"user_id": 1, "_id": 0})

    # bans
    async def is_banned(self, user_id: int) -> bool:
        return await self.bans.find_one({"_id": user_id}) is not None

    async def ban(self, user_id: int) -> None:
        await self.bans.update_one({"_id": user_id}, {"$set": {"at": _now()}}, upsert=True)

    async def unban(self, user_id: int) -> bool:
        return (await self.bans.delete_one({"_id": user_id})).deleted_count > 0

    # download history and statistics
    async def log_download(self, user_id: int, platform: str, kind: str, title: str, url: str) -> None:
        await self.downloads.insert_one({"user_id": user_id, "platform": platform, "kind": kind, "title": title[:200],
                                         "url": url, "at": _now()})

    async def recent_downloads(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        return await self.downloads.find({"user_id": user_id}).sort("at", -1).limit(limit).to_list()

    async def total_downloads(self, user_id: int) -> int:
        return await self.downloads.count_documents({"user_id": user_id})

    async def download_stats(self) -> dict[str, Any]:
        since = _now() - WINDOW
        rows = await (await self.downloads.aggregate([{"$group": {"_id": "$platform", "n": {"$sum": 1}}}])).to_list()
        return {"total": sum(r["n"] for r in rows), "by_platform": {r["_id"]: r["n"] for r in rows},
                "today": await self.downloads.count_documents({"at": {"$gt": since}})}

    # premium
    async def is_premium(self, user_id: int) -> bool:
        return await self.subs.find_one({"user_id": user_id, "expiry_date": {"$gt": _now()}}) is not None

    async def make_premium(self, user_id: int, days: int) -> datetime:
        now = _now()
        doc = await self.subs.find_one_and_update(
            {"user_id": user_id},
            [{"$set": {"expiry_date": {"$add": [{"$max": [{"$ifNull": ["$expiry_date", now]}, now]},
                                                days * 86_400_000]}}}],
            upsert=True, return_document=True)
        return _aware(doc["expiry_date"])

    async def premium_expiry(self, user_id: int) -> datetime | None:
        doc = await self.subs.find_one({"user_id": user_id, "expiry_date": {"$gt": _now()}}, sort=[("expiry_date", -1)])
        return _aware(doc["expiry_date"]) if doc else None

    async def remove_premium(self, user_id: int) -> bool:
        return (await self.subs.delete_many({"user_id": user_id})).deleted_count > 0

    async def list_premium(self, limit: int = 30) -> list[dict[str, Any]]:
        return await self.subs.find({"expiry_date": {"$gt": _now()}}).sort("expiry_date", 1).limit(limit).to_list()

    # free-tier limits: FREE_DAILY_LIMIT downloads per 24h window, COOLDOWN_MINUTES between downloads
    async def free_left(self, user_id: int) -> int:
        doc = await self.limits.find_one({"_id": user_id})
        if not doc or _aware(doc["window_start"]) + WINDOW <= _now():
            return self.cfg.FREE_DAILY_LIMIT
        return max(0, self.cfg.FREE_DAILY_LIMIT - doc.get("count", 0))

    async def limit_reason(self, user_id: int) -> str | None:
        """Why a free user can't download right now, or None if they can."""
        doc = await self.limits.find_one({"_id": user_id})
        if not doc:
            return None
        now = _now()
        window_end = _aware(doc["window_start"]) + WINDOW
        if doc.get("count", 0) >= self.cfg.FREE_DAILY_LIMIT and window_end > now:
            return (f"You've used your {self.cfg.FREE_DAILY_LIMIT} free downloads for today. "
                    f"Try again in {_fmt_wait(window_end - now)}, or get Premium for unlimited downloads.")
        if doc.get("last") and (ready := _aware(doc["last"]) + timedelta(minutes=self.cfg.COOLDOWN_MINUTES)) > now:
            return f"Please wait {_fmt_wait(ready - now)} before your next download, or get Premium to skip the wait."
        return None

    async def consume(self, user_id: int) -> str | None:
        """Atomically take one free download. Returns None on success, else the reason it was refused."""
        now = _now()
        window_cut, cool_cut = now - WINDOW, now - timedelta(minutes=self.cfg.COOLDOWN_MINUTES)
        expired = {"$lt": [{"$ifNull": ["$window_start", EPOCH]}, window_cut]}
        try:
            await self.limits.update_one(
                {"_id": user_id,
                 "$and": [{"$or": [{"window_start": {"$lt": window_cut}}, {"count": {"$lt": self.cfg.FREE_DAILY_LIMIT}}]},
                          {"$or": [{"last": {"$lt": cool_cut}}, {"last": {"$exists": False}}]}]},
                [{"$set": {"window_start": {"$cond": [expired, now, "$window_start"]},
                           "count": {"$cond": [expired, 1, {"$add": [{"$ifNull": ["$count", 0]}, 1]}]},
                           "prev_last": {"$ifNull": ["$last", "$$REMOVE"]},
                           "last": now}}],
                upsert=True)
        except DuplicateKeyError:  # the user's doc exists but didn't match: they are over a limit
            return await self.limit_reason(user_id) or "Please try again in a moment."
        return None

    async def refund(self, user_id: int) -> None:
        """Give back a download that failed, including the cooldown it started."""
        await self.limits.update_one({"_id": user_id}, [{"$set": {
            "count": {"$max": [0, {"$subtract": ["$count", 1]}]},
            "last": {"$ifNull": ["$prev_last", "$$REMOVE"]}}}])

    # pending selections: the link + options shown as inline buttons (callback_data is limited to 64 bytes)
    async def save_pending(self, user_id: int, url: str, title: str, platform: str,
                           options: list[dict[str, Any]]) -> str:
        token = secrets.token_hex(6)
        await self.pending.insert_one({"_id": token, "user_id": user_id, "url": url, "title": title,
                                       "platform": platform, "options": options, "created_at": _now()})
        return token

    async def get_pending(self, token: str) -> dict[str, Any] | None:
        return await self.pending.find_one({"_id": token})

    async def take_pending(self, token: str, user_id: int) -> dict[str, Any] | None:
        return await self.pending.find_one_and_delete({"_id": token, "user_id": user_id})
