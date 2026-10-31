"""Permanent activity log in MongoDB, shared by the website API and the Telegram bot.

Logging must never affect users: writes run in the background and failures are only logged.
Website visitors are stored as a salted hash of their IP, so raw IPs are never kept.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pymongo import AsyncMongoClient, ReturnDocument

from .config import Settings

log = logging.getLogger("allsave.activity")
Source = Literal["bot", "web"]


class ActivityLog:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._client: AsyncMongoClient[dict[str, Any]] | None = None
        self._salt: bytes | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.s.mongo_uri)

    def _db(self) -> Any:
        if self._client is None:  # created on first use, inside the running event loop
            self._client = AsyncMongoClient(self.s.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=10000)
        return self._client[self.s.activity_db]

    async def _hash(self, ip: str) -> str:
        if self._salt is None:  # stored in MongoDB so a visitor keeps the same hash across restarts
            doc = await self._db()["meta"].find_one_and_update(
                {"_id": "activity_salt"}, {"$setOnInsert": {"value": secrets.token_hex(32)}},
                upsert=True, return_document=ReturnDocument.AFTER)
            self._salt = doc["value"].encode()
        return hmac.new(self._salt, ip.encode(), hashlib.sha256).hexdigest()[:16]

    def record(self, source: Source, event: str, actor: int | str | None = None, **fields: Any) -> None:
        """Queue one event. `actor` is a Telegram user id, or a raw IP for the website (hashed before storing)."""
        if not self.enabled:
            return
        doc = {"at": datetime.now(timezone.utc), "source": source, "event": event, "actor": actor, **fields}
        task = asyncio.create_task(self._write(doc))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write(self, doc: dict[str, Any]) -> None:
        try:
            if doc["source"] == "web" and doc["actor"] is not None:
                doc["actor"] = await self._hash(str(doc["actor"]))
            await self._db()["activity"].insert_one(doc)
        except Exception as e:
            log.warning("Activity event %s not saved: %s", doc["event"], type(e).__name__)

    async def init(self) -> None:
        if self.enabled:
            try:
                col = self._db()["activity"]
                await col.create_index([("at", -1)])
                await col.create_index([("actor", 1), ("at", -1)])
            except Exception as e:
                log.warning("Activity indexes not created: %s", type(e).__name__)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.close()

    async def recent(self, actor: int | str, limit: int = 15) -> list[dict[str, Any]]:
        return await self._db()["activity"].find({"actor": actor}).sort("at", -1).limit(limit).to_list()

    async def _group(self, match: dict[str, Any], key: Any, limit: int = 0) -> list[dict[str, Any]]:
        pipeline: list[dict[str, Any]] = [{"$match": match}, {"$group": {"_id": key, "n": {"$sum": 1}}},
                                          {"$sort": {"n": -1}}]
        if limit:
            pipeline.append({"$limit": limit})
        return await (await self._db()["activity"].aggregate(pipeline)).to_list()

    async def summary(self, days: int) -> dict[str, Any]:
        col = self._db()["activity"]
        since = {"at": {"$gte": datetime.now(timezone.utc) - timedelta(days=days)}}
        events = await self._group(since, {"source": "$source", "event": "$event"})
        users = await self._group({**since, "actor": {"$ne": None}}, {"source": "$source", "actor": "$actor"})
        platforms = await self._group({**since, "event": "download_ok"}, "$platform")
        errors = await self._group({**since, "event": {"$in": ["fetch_failed", "download_failed"]}}, "$code", 5)
        top = await self._group({**since, "source": "bot", "event": "download_ok"}, "$actor", 5)
        daily = await self._group({**since, "event": "download_ok"}, {
            "$dateToString": {"format": "%m-%d", "date": "$at"}})
        counts: dict[str, dict[str, int]] = {"bot": {}, "web": {}}
        for row in events:
            counts.setdefault(row["_id"]["source"], {})[row["_id"]["event"]] = row["n"]
        return {
            "days": days, "counts": counts, "total": await col.estimated_document_count(),
            "bot_users": sum(r["_id"]["source"] == "bot" for r in users),
            "web_users": sum(r["_id"]["source"] == "web" for r in users),
            "platforms": {r["_id"] or "unknown": r["n"] for r in platforms},
            "errors": {r["_id"] or "unknown": r["n"] for r in errors},
            "top_users": [(r["_id"], r["n"]) for r in top],
            "daily": sorted((r["_id"], r["n"]) for r in daily)[-7:],
        }
