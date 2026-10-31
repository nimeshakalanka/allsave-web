"""All Save Bot, running inside the backend process and using the same job system as the web API."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from pyrogram import Client, enums, errors, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from ..errors import APIError
from ..service import AllSave
from .config import TelegramConfig
from .db import Store
from .effects import FIRE, HEART, PARTY, send_effect

log = logging.getLogger("allsave.telegram")
HTML = enums.ParseMode.HTML
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
Kb = list[list[InlineKeyboardButton]]
LINE = "━━━━━━━━━━━━━━━"
PLATFORM_ICONS = {"tiktok": "🎵", "youtube": "▶️", "instagram": "📸", "facebook": "📘"}
BANNED_TEXT = "🚫 <b>Access blocked</b>\nYou can't use this bot right now."


class AllSaveBot(Client):
    def __init__(self, cfg: TelegramConfig, svc: AllSave) -> None:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        super().__init__("all_save", api_id=cfg.API_ID, api_hash=cfg.API_HASH, bot_token=cfg.BOT_TOKEN,
                         workdir=str(cfg.DATA_DIR), workers=32, parse_mode=HTML)
        self.cfg, self.svc, self.store = cfg, svc, Store(cfg)
        self.link = f"https://t.me/{cfg.BOT_USERNAME}"
        register(self)

    async def shutdown(self) -> None:
        await self.stop()
        await self.store.close()

    # keyboards and texts depend on config
    def start_kb(self) -> Kb:
        return [[InlineKeyboardButton("👤 My account", callback_data="ACCOUNT_BUTTON"),
                 InlineKeyboardButton("📜 History", callback_data="HISTORY_BUTTON")],
                [InlineKeyboardButton("💎 Premium", callback_data="PREMIUM_BUTTON"),
                 InlineKeyboardButton("📖 Help", callback_data="COMMAND_BUTTON")],
                [InlineKeyboardButton("🎁 Invite friends", callback_data="REFER_BUTTON")],
                [InlineKeyboardButton("➕ Add me to your group", url=f"{self.link}?startgroup")]]

    def share_kb(self) -> Kb:
        return [[InlineKeyboardButton("Share ☘️", url="https://t.me/share/url?url=Check%20out%20this%20awesome%20bot%20for%20downloading%20videos%21%20%F0%9F%94%A5%0A%0AAll%20Save%20Bot%20%E2%98%98%EF%B8%8F%20%3A%20%20%40" + self.cfg.BOT_USERNAME)]]

    def pay_kb(self) -> Kb:
        return [[InlineKeyboardButton("💳 Pay now", url=self.cfg.DEV_LINK)], GOBACK[0]]

    def start_text(self, mention: str) -> str:
        return START_TEXT.format(mention=mention, line=LINE)

    async def premium_text(self) -> str:
        conf = await self.store.get_settings()
        return P_TEXT.format(limit=conf["free_daily_limit"], cooldown=conf["cooldown_minutes"],
                             days=self.cfg.PREMIUM_DAYS, line=LINE, refer_count=conf["refer_count"],
                             refer_days=conf["refer_days"])

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.cfg.SUDO


GOBACK = [[InlineKeyboardButton("🔙 Back", callback_data="START_BUTTON")]]
P_BUTTON = [[InlineKeyboardButton("💎 Get Premium", callback_data="PREMIUM_BUTTON")]]
ADMIN_KB = [[InlineKeyboardButton("📊 Stats", callback_data="ADM:stats"),
             InlineKeyboardButton("💎 Premium users", callback_data="ADM:premium")],
            [InlineKeyboardButton("📈 Activity (7 days)", callback_data="ADM:activity"),
             InlineKeyboardButton("🖥 System", callback_data="ADM:system")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ADM:settings"),
             InlineKeyboardButton("📖 Commands", callback_data="ADM:help")]]

START_TEXT = """👋 <b>Hey {mention}, welcome to All Save!</b>
{line}
Send me a video link and I'll fetch it in seconds.

🎵 TikTok  ▶️ YouTube
📸 Instagram  📘 Facebook

🎬 Choose your quality
🎧 Or grab just the MP3
✨ TikTok without watermark when available
{line}
<i>Just paste a link to start 👇</i>"""
CMD_TEXT = f"""📖 <b>How to use All Save</b>
{LINE}
<b>1.</b> Send a video link
<b>2.</b> Tap a quality or MP3
<b>3.</b> Get your file right here

<b>Commands</b>
/start — Main menu
/me — Your plan and usage
/history — Your recent downloads
/premium — Unlimited downloads
/refer — Invite friends, get free premium
/help — This message

<i>Please save only content you have the right to, and respect creators.</i>"""
P_TEXT = """💎 <b>All Save Premium</b>
{line}
🆓 <b>Free</b>
• {limit} downloads per day
• {cooldown} min wait between downloads

💎 <b>Premium</b> — $3 for {days} days
• Unlimited downloads
• No waiting

🎁 Or invite {refer_count} friends with /refer and get {refer_days} days free

💳 PayPal, Crypto or Bank Transfer (Sri Lanka)
{line}
<i>Tap Pay now to contact the developer.</i>"""
HELP_LINKS = """📖 <b>Send me a link like these</b>
{line}
<code>https://www.tiktok.com/@user/video/7123456789</code>
<code>https://youtu.be/dQw4w9WgXcQ</code>
<code>https://www.instagram.com/reel/Cabc123/</code>
<code>https://www.facebook.com/share/v/19ic1QBvm4/</code>

Then tap a quality or MP3. Use /me to see your plan."""
ADMIN_HELP = f"""🛠 <b>Admin commands</b>
{LINE}
/admin — This panel
/settings — Limits, free periods, referral reward
/stats — Users and downloads
/activity <code>[days]</code> — Bot and website activity
/log <code>id</code> — A user's recent activity
/sub <code>id [days]</code> — Give or extend premium
/unsub <code>id</code> — Remove premium
/premiumlist — Active premium users
/user <code>id</code> — View a user
/ban <code>id</code> · /unban <code>id</code> — Block or unblock
/bcast — Reply to a message to broadcast it"""


def progress_bar(percent: int, width: int = 10) -> str:
    filled = min(width, max(0, round(percent / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def fmt_size(n: int | None) -> str:
    if not n:
        return ""
    mb = n / 1048576
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB" if mb >= 1 else "<1 MB"


def fmt_duration(s: int | None) -> str:
    if not s:
        return ""
    h, m = s // 3600, s % 3600 // 60
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def fmt_date(dt: datetime) -> str:
    return f"{dt.astimezone(timezone.utc):%d %b %Y}"


def fmt_time(dt: datetime) -> str:
    return f"{dt.astimezone(timezone.utc):%d %b %H:%M} UTC"


def parse_duration(text: str) -> timedelta | None:
    """'12h' or '7d' to a timedelta; None if invalid."""
    match = re.fullmatch(r"(\d{1,4})([hd])", text.lower())
    if not match or not int(match[1]):
        return None
    return timedelta(hours=int(match[1])) if match[2] == "h" else timedelta(days=int(match[1]))


def option_rows(options: list[dict[str, Any]], token: str) -> Kb:
    """Two video buttons per row, audio on its own row, then Cancel."""
    def button(i: int, o: dict[str, Any]) -> InlineKeyboardButton:
        size = fmt_size(o["size"])
        icon = "🎬" if o["kind"] == "video" else "🎧"
        return InlineKeyboardButton(f"{icon} {o['label']}{f' · {size}' if size else ''}", callback_data=f"dl:{token}:{i}")

    indexed = list(enumerate(options))
    videos = [button(i, o) for i, o in indexed if o["kind"] == "video"]
    rows = [videos[i:i + 2] for i in range(0, len(videos), 2)]
    rows += [[button(i, o)] for i, o in indexed if o["kind"] == "audio"]
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"cx:{token}")])
    return rows


async def edit(msg: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        if msg.photo:
            await msg.edit_caption(text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except (errors.MessageNotModified, errors.FloodWait):
        pass


async def show(cb: CallbackQuery, text: str, buttons: Kb) -> None:
    """Replace the callback's message, whether it is a photo caption or plain text."""
    try:
        if cb.message.photo:
            await cb.edit_message_caption(text[:1024], reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await cb.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    except errors.MessageNotModified:
        pass


async def celebrate(client: AllSaveBot, chat_id: int, text: str, effect: str) -> None:
    """Send with a full-screen effect, falling back to a plain message (effects only work in private chats)."""
    if not await send_effect(client.cfg.BOT_TOKEN, chat_id, text, effect):
        try:
            await client.send_message(chat_id, text, disable_web_page_preview=True)
        except errors.RPCError:
            log.warning("Could not message %s", chat_id)


async def react(client: AllSaveBot, message: Message, emoji: str) -> None:
    try:
        await client.send_reaction(message.chat.id, message.id, emoji)
    except errors.RPCError:
        pass  # reactions are decoration; some chats disable them


async def in_channel(client: AllSaveBot, user_id: int) -> bool:
    if not client.cfg.CHID:
        return True
    try:
        await client.get_chat_member(client.cfg.CHID, user_id)
        return True
    except errors.UserNotParticipant:
        return False
    except (errors.RPCError, KeyError, ValueError):  # a wrong CHID must not silently lock every user out
        log.exception("Could not check channel %s; is the bot an admin there?", client.cfg.CHID)
        return True


async def send_welcome(client: AllSaveBot, user: User, photo: str) -> None:
    text, markup = client.start_text(user.mention(style=HTML)), InlineKeyboardMarkup(client.start_kb())
    try:
        await client.send_photo(user.id, photo=photo, caption=text, reply_markup=markup)
    except errors.RPCError:  # Telegram could not fetch the image; the user still gets a reply
        log.exception("Welcome photo failed")
        await client.send_message(user.id, text, reply_markup=markup)


async def account_text(client: AllSaveBot, user_id: int, name: str, *, admin_view: bool = False) -> str:
    store = client.store
    expiry = await store.premium_expiry(user_id)
    total = await store.total_downloads(user_id)
    promo = await store.promo_until()
    limit = (await store.get_settings())["free_daily_limit"]
    if promo and not expiry:
        plan = f"🎁 <b>Free unlimited</b> for everyone until {fmt_time(promo)}"
        usage = "♾ Unlimited downloads"
    elif expiry:
        days = (expiry - datetime.now(timezone.utc)).days
        plan = f"💎 <b>Premium</b> until {fmt_date(expiry)} ({days} days left)"
        usage = "♾ Unlimited downloads"
    else:
        left = await store.free_left(user_id)
        plan = "🆓 <b>Free</b>"
        usage = f"{progress_bar(int(left / limit * 100))} {left}/{limit} left today"
    lines = [f"👤 <b>{name}</b>", LINE, f"🆔 <code>{user_id}</code>", f"🏷 {plan}", f"📥 {usage}",
             f"📊 {total} total downloads"]
    if admin_view and await store.is_banned(user_id):
        lines.append("🚫 <b>Banned</b>")
    if not expiry and not promo and not admin_view and (reason := await store.limit_reason(user_id)):
        lines += [LINE, f"⏳ <i>{html.escape(reason)}</i>"]
    return "\n".join(lines)


async def history_text(client: AllSaveBot, user_id: int) -> str:
    rows = await client.store.recent_downloads(user_id)
    if not rows:
        return f"📜 <b>Your history</b>\n{LINE}\nNothing yet. Send me a link to get started!"
    items = [f"{PLATFORM_ICONS.get(r['platform'], '🎬')} <a href=\"{html.escape(r['url'], quote=True)}\">"
             f"{html.escape(r['title'][:50])}</a>\n     <i>{fmt_date(r['at'])}{' · MP3' if r['kind'] == 'audio' else ''}</i>"
             for r in rows]
    return f"📜 <b>Your last {len(rows)} downloads</b>\n{LINE}\n" + "\n".join(items)


# ---- basic commands -------------------------------------------------------------------------------------------

async def start(client: AllSaveBot, message: Message) -> None:
    user = message.from_user
    if not user:
        return
    log.info("/start from %s in %s", user.id, message.chat.type)
    arg = message.command[1] if len(message.command) > 1 else ""
    ref = int(arg[4:]) if arg.startswith("ref_") and arg[4:].isdigit() else None
    if await client.store.is_banned(user.id):
        await message.reply_text(BANNED_TEXT)
        return
    if not await in_channel(client, user.id):
        await message.reply_text(
            f"🔒 <b>One quick step</b>\n{LINE}\nJoin our <a href=\"{client.cfg.CHANNEL_LINK}\">updates channel</a> to use this bot, then tap the button below.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I've joined", f"chk:{ref or 0}")]]),
            disable_web_page_preview=True)
        return
    if message.chat.type == enums.ChatType.PRIVATE:
        is_new = await client.store.add_user(user.id)
        client.svc.activity.record("bot", "new_user" if is_new else "start", user.id)
        await send_welcome(client, user, "https://telegra.ph/file/0351e7d492b896af35c1f.png")
        if is_new:
            await celebrate(client, user.id, "🎉 <b>You're in!</b> Your first downloads are on us.", PARTY)
            if ref:
                await credit_referral(client, user.id, ref)
    else:
        await client.store.add_group(message.chat.id)
        await message.reply_text(
            f"👋 <b>Hello {html.escape(user.first_name or '')}!</b>\nSend a video link here, or open me in private for your account.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Open private chat", url=f"{client.link}?start=start")]]))


async def chk(client: AllSaveBot, cb: CallbackQuery) -> None:
    if not await in_channel(client, cb.from_user.id):
        await cb.answer("Please join the channel first 🙏", show_alert=True)
        return
    await cb.answer("Thanks for joining! 🎉")
    if cb.message.chat.type == enums.ChatType.PRIVATE:
        is_new = await client.store.add_user(cb.from_user.id)
        ref = int(cb.data.split(":")[1]) if ":" in cb.data else 0
        if is_new and ref:
            await credit_referral(client, cb.from_user.id, ref)
        await send_welcome(client, cb.from_user, "https://telegra.ph/file/6aa7c012295bbf509aaa7.png")


async def credit_referral(client: AllSaveBot, user_id: int, by: int) -> None:
    count = await client.store.add_referral(user_id, by)
    if count is None:
        return
    client.svc.activity.record("bot", "referral", by, invited=user_id)
    conf = await client.store.get_settings()
    need, days = conf["refer_count"], conf["refer_days"]
    if count % need == 0:
        expiry = await client.store.make_premium(by, days)
        await celebrate(client, by, f"🎁 <b>You earned {days} days of Premium!</b>\n{LINE}\n{need} friends joined with your link. Active until <b>{fmt_date(expiry)}</b>. 🎉", PARTY)
        return
    try:
        await client.send_message(by, f"🎉 <b>A friend joined!</b> {count % need}/{need} towards {days} days of Premium.")
    except errors.RPCError:
        pass


async def refer_text(client: AllSaveBot, user_id: int) -> tuple[str, Kb]:
    conf = await client.store.get_settings()
    need, days = conf["refer_count"], conf["refer_days"]
    count = await client.store.referral_count(user_id)
    link = f"{client.link}?start=ref_{user_id}"
    text = (f"🎁 <b>Invite friends, get Premium</b>\n{LINE}\nInvite <b>{need} new users</b> and get <b>{days} days of Premium</b>. "
            f"It repeats every {need} friends!\n\n{progress_bar(int((count % need) / need * 100))} {count % need}/{need}\n"
            f"👥 Total invited: <b>{count}</b>\n\n🔗 Your link:\n<code>{link}</code>")
    share = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote('Download TikTok, Instagram and Facebook videos in one tap!')}"
    return text, [[InlineKeyboardButton("📤 Share my link", url=share)]]


async def menu_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    user = cb.from_user
    client.svc.activity.record("bot", "button", user.id, name=cb.data.removesuffix("_BUTTON").lower())
    if cb.data == "START_BUTTON":
        await show(cb, client.start_text(user.mention(style=HTML)), client.start_kb())
    elif cb.data == "COMMAND_BUTTON":
        await show(cb, CMD_TEXT, GOBACK)
    elif cb.data == "PREMIUM_BUTTON":
        await show(cb, await client.premium_text(), client.pay_kb())
    elif cb.data == "REFER_BUTTON":
        text, buttons = await refer_text(client, user.id)
        await show(cb, text, buttons + GOBACK)
    elif cb.data == "ACCOUNT_BUTTON":
        await show(cb, await account_text(client, user.id, html.escape(user.first_name or "You")), GOBACK)
    elif cb.data == "HISTORY_BUTTON":
        await show(cb, await history_text(client, user.id), GOBACK)
    await cb.answer()


async def help_cmd(_: AllSaveBot, message: Message) -> None:
    await message.reply_text(f"{HELP_LINKS.format(line=LINE)}\n\n{CMD_TEXT}", disable_web_page_preview=True)


async def premium_cmd(client: AllSaveBot, message: Message) -> None:
    await message.reply_text(await client.premium_text(), reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("💳 Pay now", url=client.cfg.DEV_LINK)]]))


async def refer_cmd(client: AllSaveBot, message: Message) -> None:
    if message.from_user:
        text, buttons = await refer_text(client, message.from_user.id)
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def me_cmd(client: AllSaveBot, message: Message) -> None:
    if user := message.from_user:
        await message.reply_text(await account_text(client, user.id, html.escape(user.first_name or "You")),
                                 reply_markup=InlineKeyboardMarkup(P_BUTTON))


async def history_cmd(client: AllSaveBot, message: Message) -> None:
    if message.from_user:
        await message.reply_text(await history_text(client, message.from_user.id), disable_web_page_preview=True)


# ---- admin commands -------------------------------------------------------------------------------------------

def target_id(message: Message) -> int | None:
    """User id from the command argument, or from the replied-to message."""
    args = message.command[1:]
    if args and args[0].lstrip("-").isdigit():
        return int(args[0])
    reply = message.reply_to_message
    return reply.from_user.id if reply and reply.from_user else None


async def stats_text(client: AllSaveBot) -> str:
    users, groups, premium = await client.store.counts()
    stats = await client.store.download_stats()
    platforms = "\n".join(f"{PLATFORM_ICONS.get(k, '🎬')} {k.title()}: <b>{v}</b>" for k, v in
                          sorted(stats["by_platform"].items(), key=lambda kv: -kv[1])) or "<i>No downloads yet</i>"
    return (f"📊 <b>Bot statistics</b>\n{LINE}\n🙋 Users: <b>{users}</b>\n👥 Groups: <b>{groups}</b>\n"
            f"💎 Active premium: <b>{premium}</b>\n{LINE}\n📥 Last 24h: <b>{stats['today']}</b>\n"
            f"📦 All time: <b>{stats['total']}</b>\n{LINE}\n{platforms}")


async def premium_list_text(client: AllSaveBot) -> str:
    rows = await client.store.list_premium()
    if not rows:
        return f"💎 <b>Premium users</b>\n{LINE}\nNo active subscriptions."
    items = [f"<code>{r['user_id']}</code> — {fmt_date(r['expiry_date'])}" for r in rows]
    return f"💎 <b>Premium users</b> (soonest expiry first)\n{LINE}\n" + "\n".join(items)


def system_text(client: AllSaveBot) -> str:
    jobs = client.svc.jobs.jobs.values()
    active = sum(not j.finished for j in jobs)
    disk = shutil.disk_usage(client.svc.s.work_root)
    return (f"🖥 <b>System</b>\n{LINE}\n⚙️ Active jobs: <b>{active}</b> / {client.svc.s.max_concurrent_jobs} at once\n"
            f"🗂 Jobs held: <b>{len(jobs)}</b>\n💾 Disk free: <b>{fmt_size(disk.free)}</b> of {fmt_size(disk.total)}")


EVENT_LABELS = {"start": "Started the bot", "new_user": "Joined", "command": "Command", "button": "Menu button",
                "fetch_ok": "Fetched a link", "fetch_failed": "Fetch failed", "download_ok": "Downloaded",
                "download_failed": "Download failed", "delivered": "Received file", "upload_failed": "Upload failed",
                "limit_hit": "Hit the free limit", "referral": "Invited a user"}


def activity_text(data: dict[str, Any]) -> str:
    bot, web = data["counts"].get("bot", {}), data["counts"].get("web", {})
    lines = [f"📈 <b>Activity, last {data['days']} days</b>", LINE, "🤖 <b>Telegram bot</b>",
             f"👥 Active users: <b>{data['bot_users']}</b>", f"🆕 New users: <b>{bot.get('new_user', 0)}</b>",
             f"🔗 Links sent: <b>{bot.get('fetch_ok', 0) + bot.get('fetch_failed', 0)}</b>",
             f"✅ Downloads: <b>{bot.get('download_ok', 0)}</b>", f"❌ Failed: <b>{bot.get('download_failed', 0)}</b>",
             f"⏳ Free limit hits: <b>{bot.get('limit_hit', 0)}</b>", LINE, "🌐 <b>Website</b>",
             f"👥 Unique visitors: <b>{data['web_users']}</b>", f"👀 Visits: <b>{web.get('visit', 0)}</b>",
             f"🔗 Links fetched: <b>{web.get('fetch_ok', 0) + web.get('fetch_failed', 0)}</b>",
             f"✅ Downloads: <b>{web.get('download_ok', 0)}</b>", f"❌ Failed: <b>{web.get('download_failed', 0)}</b>"]
    if data["platforms"]:
        lines += [LINE, "📦 <b>By platform</b>"]
        lines += [f"{PLATFORM_ICONS.get(k, '🎬')} {k.title()}: <b>{v}</b>" for k, v in data["platforms"].items()]
    if data["daily"]:
        lines += [LINE, "🗓 <b>Daily downloads</b> (UTC)"]
        lines += [f"<code>{day}</code>  {n}" for day, n in data["daily"]]
    if data["top_users"]:
        lines += [LINE, "🏆 <b>Top bot users</b>"]
        lines += [f"<code>{user}</code> — {n}" for user, n in data["top_users"]]
    if data["errors"]:
        lines += [LINE, "⚠️ <b>Top errors</b>"]
        lines += [f"<code>{html.escape(str(k))}</code>: {v}" for k, v in data["errors"].items()]
    lines += [LINE, f"🗄 All events stored: <b>{data['total']}</b>"]
    return "\n".join(lines)


async def activity_cmd(client: AllSaveBot, message: Message) -> None:
    if not client.svc.activity.enabled:
        await message.reply_text("Activity log is off: set MONGO_URI.")
        return
    args = message.command[1:]
    days = int(args[0]) if args and args[0].isdigit() else 7
    await message.reply_text(activity_text(await client.svc.activity.summary(max(1, min(days, 3650)))))


async def log_cmd(client: AllSaveBot, message: Message) -> None:
    if (user_id := target_id(message)) is None:
        await message.reply_text("Usage: <code>/log user_id</code>")
        return
    rows = await client.svc.activity.recent(user_id)
    if not rows:
        await message.reply_text(f"No activity recorded for <code>{user_id}</code>.")
        return
    items = []
    for r in rows:
        detail = r.get("platform") or r.get("name") or r.get("code") or ""
        items.append(f"<code>{r['at']:%d %b %H:%M}</code> {EVENT_LABELS.get(r['event'], r['event'])}"
                     f"{f' · {html.escape(str(detail))}' if detail else ''}")
    await message.reply_text(f"🧾 <b>Activity of</b> <code>{user_id}</code> (UTC)\n{LINE}\n" + "\n".join(items))


def settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [b("➖ Daily limit", callback_data="SET:limit:-1"), b("➕ Daily limit", callback_data="SET:limit:1")],
        [b("➖ Wait 5 min", callback_data="SET:cool:-5"), b("➕ Wait 5 min", callback_data="SET:cool:5")],
        [b("🎁 Free 1h", callback_data="SET:free:1h"), b("🎁 Free 24h", callback_data="SET:free:24h"),
         b("🎁 Free 7d", callback_data="SET:free:7d")],
        [b("⏹ End free period", callback_data="SET:free:off")]])


async def settings_text(client: AllSaveBot) -> str:
    conf, promo = await client.store.get_settings(), await client.store.promo_until()
    return (f"⚙️ <b>Bot settings</b>\n{LINE}\n📥 Free daily limit: <b>{conf['free_daily_limit']}</b>\n"
            f"⏳ Wait between downloads: <b>{conf['cooldown_minutes']} min</b>\n"
            f"🎁 Referral reward: <b>{conf['refer_count']} friends = {conf['refer_days']} days</b>\n"
            f"🎉 Free for everyone: <b>{f'until {fmt_time(promo)}' if promo else 'off'}</b>\n{LINE}\n"
            "<b>Custom values</b>\n<code>/settings limit 4</code>\n<code>/settings wait 15</code>\n"
            "<code>/settings free 12h</code> or <code>3d</code> or <code>off</code>\n<code>/settings refer 3 3</code> (friends, days)")


async def apply_setting(client: AllSaveBot, key: str, values: list[str]) -> str | None:
    """Apply one change; returns an error message, or None on success."""
    store = client.store
    conf = await store.get_settings()
    try:
        if key == "limit":
            await store.update_settings(free_daily_limit=max(1, min(int(values[0]), 1000)))
        elif key == "wait":
            await store.update_settings(cooldown_minutes=max(0, min(int(values[0]), 1440)))
        elif key == "refer":
            await store.update_settings(refer_count=max(1, min(int(values[0]), 100)),
                                        refer_days=max(1, min(int(values[1]), 365)))
        elif key == "free":
            if values[0].lower() == "off":
                await store.update_settings(free_until=None)
            elif duration := parse_duration(values[0]):
                await store.update_settings(free_until=datetime.now(timezone.utc) + duration)
                client.svc.activity.record("bot", "free_period", None, hours=duration.total_seconds() / 3600)
            else:
                return "Use a time like <code>12h</code> or <code>3d</code>, or <code>off</code>."
        else:
            return f"Unknown setting. Current limit is {conf['free_daily_limit']}."
    except (IndexError, ValueError):
        return "That value isn't valid. See the examples in /settings."
    return None


async def settings_cmd(client: AllSaveBot, message: Message) -> None:
    args = message.command[1:]
    if args and (problem := await apply_setting(client, args[0].lower(), args[1:])):
        await message.reply_text(f"⚠️ {problem}")
        return
    await message.reply_text(await settings_text(client), reply_markup=settings_kb())


async def settings_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    if not client.is_admin(cb.from_user.id):
        await cb.answer("Admins only.", show_alert=True)
        return
    _, key, value = cb.data.split(":")
    conf = await client.store.get_settings()
    if key == "limit":
        await apply_setting(client, "limit", [str(conf["free_daily_limit"] + int(value))])
    elif key == "cool":
        await apply_setting(client, "wait", [str(conf["cooldown_minutes"] + int(value))])
    else:
        await apply_setting(client, "free", [value])
    await show(cb, await settings_text(client), settings_kb().inline_keyboard)
    await cb.answer("Saved ✅")


async def admin_cmd(_: AllSaveBot, message: Message) -> None:
    await message.reply_text(f"🛠 <b>Admin panel</b>\n{LINE}\nChoose a section:", reply_markup=InlineKeyboardMarkup(ADMIN_KB))


async def admin_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    if not client.is_admin(cb.from_user.id):
        await cb.answer("Admins only.", show_alert=True)
        return
    section = cb.data.split(":", 1)[1]
    if section == "settings":
        await show(cb, await settings_text(client), settings_kb().inline_keyboard)
        await cb.answer()
        return
    text = {"help": ADMIN_HELP}.get(section)
    if section == "stats":
        text = await stats_text(client)
    elif section == "activity":
        text = (activity_text(await client.svc.activity.summary(7)) if client.svc.activity.enabled
                else "Activity log is off: set MONGO_URI.")
    elif section == "premium":
        text = await premium_list_text(client)
    elif section == "system":
        text = system_text(client)
    if text:
        await show(cb, text, ADMIN_KB)
    await cb.answer()


async def sub_cmd(client: AllSaveBot, message: Message) -> None:
    args = message.command[1:]
    try:
        user_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else client.cfg.PREMIUM_DAYS
    except (IndexError, ValueError):
        await message.reply_text("Usage: <code>/sub user_id [days]</code>")
        return
    expiry = await client.store.make_premium(user_id, days)
    await message.reply_text(f"💎 <code>{user_id}</code> is premium until <b>{fmt_date(expiry)}</b>.")
    await celebrate(client, user_id, f"💎 <b>Welcome to Premium!</b>\n{LINE}\nUnlimited downloads until <b>{fmt_date(expiry)}</b>. Enjoy! 🎉", HEART)


async def unsub_cmd(client: AllSaveBot, message: Message) -> None:
    if (user_id := target_id(message)) is None:
        await message.reply_text("Usage: <code>/unsub user_id</code>")
        return
    removed = await client.store.remove_premium(user_id)
    await message.reply_text(f"✅ Premium removed from <code>{user_id}</code>." if removed else "That user has no premium.")


async def user_cmd(client: AllSaveBot, message: Message) -> None:
    if (user_id := target_id(message)) is None:
        await message.reply_text("Usage: <code>/user user_id</code>")
        return
    await message.reply_text(await account_text(client, user_id, "User", admin_view=True))


async def ban_cmd(client: AllSaveBot, message: Message) -> None:
    if (user_id := target_id(message)) is None or client.is_admin(user_id):
        await message.reply_text("Usage: <code>/ban user_id</code> (admins can't be banned)")
        return
    await client.store.ban(user_id)
    await message.reply_text(f"🚫 <code>{user_id}</code> is banned.")


async def unban_cmd(client: AllSaveBot, message: Message) -> None:
    if (user_id := target_id(message)) is None:
        await message.reply_text("Usage: <code>/unban user_id</code>")
        return
    removed = await client.store.unban(user_id)
    await message.reply_text(f"✅ <code>{user_id}</code> is unbanned." if removed else "That user isn't banned.")


async def stats_cmd(client: AllSaveBot, message: Message) -> None:
    await message.reply_text(await stats_text(client))


async def premiumlist_cmd(client: AllSaveBot, message: Message) -> None:
    await message.reply_text(await premium_list_text(client))


async def bcast_cmd(client: AllSaveBot, m: Message) -> None:
    if not m.reply_to_message:
        await m.reply_text("Reply to the message you want to broadcast with /bcast.")
        return
    status = await m.reply_text("📣 <b>Broadcast started…</b>")
    success = failed = deactivated = blocked = 0
    async for doc in client.store.all_user_ids():
        user_id = doc["user_id"]
        for _ in range(2):  # one retry after a FloodWait
            try:
                await m.reply_to_message.copy(int(user_id))
                success += 1
            except errors.FloodWait as e:
                await asyncio.sleep(e.value)
                continue
            except errors.InputUserDeactivated:
                deactivated += 1
                await client.store.remove_user(user_id)
            except errors.UserIsBlocked:
                blocked += 1
            except Exception:
                failed += 1
            break
        done = success + failed + deactivated + blocked
        if done % 50 == 0:
            await edit(status, f"📣 <b>Broadcasting…</b> {done} sent so far")
    await edit(status, f"📣 <b>Broadcast finished</b>\n{LINE}\n✅ Delivered: <b>{success}</b>\n❌ Failed: <b>{failed}</b>\n"
                       f"🚫 Blocked: <b>{blocked}</b>\n👻 Deactivated: <b>{deactivated}</b>")


# ---- downloads ------------------------------------------------------------------------------------------------

async def on_link(client: AllSaveBot, message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    if await client.store.is_banned(user_id):
        await message.reply_text(BANNED_TEXT)
        return
    url = URL_RE.search(message.text).group(0)  # type: ignore[union-attr]
    await react(client, message, "⚡")
    status = await message.reply_text("🔎 <b>Fetching your video…</b>")

    premium = await client.store.has_unlimited(user_id)
    if not premium and (reason := await client.store.limit_reason(user_id)):
        client.svc.activity.record("bot", "limit_hit", user_id)
        await edit(status, f"⏳ <b>Almost there</b>\n{LINE}\n{html.escape(reason)}", InlineKeyboardMarkup(P_BUTTON))
        return
    try:
        info = await client.svc.info(url, "bot", user_id)
    except APIError as e:
        await edit(status, f"😕 <b>Couldn't get that video</b>\n{LINE}\n{html.escape(e.message)}")
        return

    options = [o.model_dump() for o in info.options]
    token = await client.store.save_pending(user_id, url, info.title, info.platform, options)
    rows = option_rows(options, token)
    meta = " · ".join(x for x in (f"{PLATFORM_ICONS.get(info.platform, '🎬')} {info.platform.title()}",
                                  f"⏱ {fmt_duration(info.duration)}" if info.duration else "") if x)
    if promo := await client.store.promo_until():
        plan = f"🎁 Free unlimited until {fmt_time(promo)}"
    else:
        plan = "💎 Premium" if premium else f"🆓 {await client.store.free_left(user_id)} free left today"
    text = f"<b>{html.escape(info.title[:300])}</b>\n{LINE}\n{meta}\n{plan}\n\n👇 <i>Choose a format</i>"
    if info.thumbnail:
        try:
            await message.reply_photo(info.thumbnail, caption=text, reply_markup=InlineKeyboardMarkup(rows))
            await status.delete()
            return
        except errors.RPCError:
            pass  # Telegram could not fetch the thumbnail; fall back to text
    await edit(status, text, InlineKeyboardMarkup(rows))


async def on_cancel(client: AllSaveBot, cb: CallbackQuery) -> None:
    if await client.store.take_pending(cb.data.split(":")[1], cb.from_user.id):
        await cb.message.delete()
    else:
        await cb.answer("This isn't your request.", show_alert=True)


async def on_pick(client: AllSaveBot, cb: CallbackQuery) -> None:
    _, token, idx = cb.data.split(":")
    user_id = cb.from_user.id
    if await client.store.is_banned(user_id):
        await cb.answer("You can't use this bot right now.", show_alert=True)
        return
    pending = await client.store.get_pending(token)
    if not pending:
        await cb.answer("This request expired. Send the link again.", show_alert=True)
        return
    if pending["user_id"] != user_id:
        await cb.answer("This isn't your request. Send your own link.", show_alert=True)
        return
    if int(idx) >= len(pending["options"]):
        await cb.answer("That option is no longer available.", show_alert=True)
        return
    premium = await client.store.has_unlimited(user_id)
    if not premium and (reason := await client.store.consume(user_id)):
        await cb.answer(reason[:200], show_alert=True)
        return
    if not await client.store.take_pending(token, user_id):  # double tap: already started
        if not premium:
            await client.store.refund(user_id)
        await cb.answer()
        return
    await cb.answer("Starting…")

    option = pending["options"][int(idx)]
    title, msg = pending["title"], cb.message
    heading = f"<b>{html.escape(title[:200])}</b>\n{LINE}"
    last_shown: tuple[str, int] | None = None

    async def on_update(state: str, progress: int) -> None:
        nonlocal last_shown
        shown = (state, progress // 10)
        if shown != last_shown:
            last_shown = shown
            body = {"queued": "⏳ <b>Waiting in queue…</b>", "processing": "⚙️ <b>Processing…</b>",
                    "downloading": f"⬇️ <b>Downloading</b>\n{progress_bar(progress)} {progress}%"}.get(state, state)
            await edit(msg, f"{heading}\n{body}")

    job_id = None
    try:
        job = await client.svc.create_job(pending["url"], option["id"], "bot", user_id)
        job_id = job.id
        job = await client.svc.wait(job.id, on_update)
        await edit(msg, f"{heading}\n📤 <b>Uploading…</b>\n<i>Big files can take a moment.</i>")
        await upload(client, cb, job.file, job.filename or job.file.name, option, title)  # type: ignore[arg-type]
    except APIError as e:
        if not premium:
            await client.store.refund(user_id)
        await edit(msg, f"😕 <b>Download failed</b>\n{LINE}\n{html.escape(e.message)}\n\n<i>This one didn't count against your limit.</i>")
        return
    except Exception:
        client.svc.activity.record("bot", "upload_failed", user_id, platform=pending.get("platform"))
        if not premium:
            await client.store.refund(user_id)
        await edit(msg, f"😕 <b>Upload failed</b>\n{LINE}\nPlease try again.\n\n<i>This one didn't count against your limit.</i>")
        raise
    finally:
        if job_id:
            client.svc.jobs.served(job_id)  # file is deleted on the next sweep
    await client.store.log_download(user_id, pending.get("platform", "unknown"), option["kind"], title, pending["url"])
    client.svc.activity.record("bot", "delivered", user_id, platform=pending.get("platform"), kind=option["kind"])
    await msg.delete()
    if cb.message.chat.type == enums.ChatType.PRIVATE:
        left = "" if premium else f"\n🆓 {await client.store.free_left(user_id)} free left today · /premium"
        await celebrate(client, user_id, f"✅ <b>Enjoy your {'audio' if option['kind'] == 'audio' else 'video'}!</b>{left}", FIRE)


async def upload(client: AllSaveBot, cb: CallbackQuery, path: Any, file_name: str, option: dict[str, Any],
                 title: str) -> None:
    chat_id = cb.message.chat.id
    caption = (f"🎬 <b>{html.escape(title[:800])}</b>\n{LINE}\n👤 <spoiler>{cb.from_user.mention(style=HTML)}</spoiler>\n"
               f"🚀 <a href=\"{client.link}\">All Save Bot ☘️</a>")
    kwargs: dict[str, Any] = {"caption": caption, "file_name": file_name,
                              "reply_to_message_id": cb.message.reply_to_message_id,
                              "reply_markup": InlineKeyboardMarkup(client.share_kb())}
    if option["kind"] == "audio":
        await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_AUDIO)
        await client.send_audio(chat_id, str(path), title=title[:64], **kwargs)
        return
    await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_VIDEO)
    try:
        await client.send_video(chat_id, str(path), supports_streaming=True, **kwargs)
    except errors.RPCError:
        await client.send_document(chat_id, str(path), **kwargs)


def logged(name: str, fn: Callable[[AllSaveBot, Message], Awaitable[None]]) -> Callable[[AllSaveBot, Message], Awaitable[None]]:
    async def wrapper(client: AllSaveBot, message: Message) -> None:
        if message.from_user:
            client.svc.activity.record("bot", "command", message.from_user.id, name=name)
        await fn(client, message)
    return wrapper


def register(app: AllSaveBot) -> None:
    sudo = filters.user(app.cfg.SUDO)
    admin = {"settings": settings_cmd, "sub": sub_cmd, "unsub": unsub_cmd, "user": user_cmd, "ban": ban_cmd, "unban": unban_cmd,
             "stats": stats_cmd, "activity": activity_cmd, "log": log_cmd, "premiumlist": premiumlist_cmd, "admin": admin_cmd, "bcast": bcast_cmd}
    public = {"start": start, "help": help_cmd, "premium": premium_cmd, "me": me_cmd, "history": history_cmd,
              "refer": refer_cmd}
    for name, fn in public.items():
        app.add_handler(MessageHandler(logged(name, fn) if name != "start" else fn, filters.command(name)))
    for name, fn in admin.items():
        app.add_handler(MessageHandler(logged(name, fn), filters.command(name) & sudo))
    app.add_handler(MessageHandler(on_link, filters.text & filters.regex(URL_RE) & ~filters.command([*public, *admin])))
    for fn, pattern in ((chk, r"^chk(:\d+)?$"), (settings_buttons, "^SET:"), (menu_buttons, "_BUTTON$"), (admin_buttons, "^ADM:"), (on_cancel, r"^cx:"),
                        (on_pick, r"^dl:[0-9a-f]{12}:\d+$")):
        app.add_handler(CallbackQueryHandler(fn, filters.regex(pattern)))
