"""All Save Bot, running inside the backend process and using the same job system as the web API."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import shutil
from datetime import datetime, timezone
from typing import Any

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
                [InlineKeyboardButton("➕ Add me to your group", url=f"{self.link}?startgroup")]]

    def share_kb(self) -> Kb:
        return [[InlineKeyboardButton("Share ☘️", url="https://t.me/share/url?url=Check%20out%20this%20awesome%20bot%20for%20downloading%20videos%21%20%F0%9F%94%A5%0A%0AAll%20Save%20Bot%20%E2%98%98%EF%B8%8F%20%3A%20%20%40" + self.cfg.BOT_USERNAME)]]

    def pay_kb(self) -> Kb:
        return [[InlineKeyboardButton("💳 Pay now", url=self.cfg.DEV_LINK)], GOBACK[0]]

    def start_text(self, mention: str) -> str:
        return START_TEXT.format(mention=mention, line=LINE)

    def premium_text(self) -> str:
        c = self.cfg
        return P_TEXT.format(limit=c.FREE_DAILY_LIMIT, cooldown=c.COOLDOWN_MINUTES, days=c.PREMIUM_DAYS, line=LINE)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.cfg.SUDO


GOBACK = [[InlineKeyboardButton("🔙 Back", callback_data="START_BUTTON")]]
P_BUTTON = [[InlineKeyboardButton("💎 Get Premium", callback_data="PREMIUM_BUTTON")]]
ADMIN_KB = [[InlineKeyboardButton("📊 Stats", callback_data="ADM:stats"),
             InlineKeyboardButton("💎 Premium users", callback_data="ADM:premium")],
            [InlineKeyboardButton("🖥 System", callback_data="ADM:system"),
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
/stats — Users and downloads
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
    store, cfg = client.store, client.cfg
    expiry = await store.premium_expiry(user_id)
    total = await store.total_downloads(user_id)
    if expiry:
        days = (expiry - datetime.now(timezone.utc)).days
        plan = f"💎 <b>Premium</b> until {fmt_date(expiry)} ({days} days left)"
        usage = "♾ Unlimited downloads"
    else:
        left = await store.free_left(user_id)
        plan = "🆓 <b>Free</b>"
        usage = f"{progress_bar(int(left / cfg.FREE_DAILY_LIMIT * 100) if cfg.FREE_DAILY_LIMIT else 0)} {left}/{cfg.FREE_DAILY_LIMIT} left today"
    lines = [f"👤 <b>{name}</b>", LINE, f"🆔 <code>{user_id}</code>", f"🏷 {plan}", f"📥 {usage}",
             f"📊 {total} total downloads"]
    if admin_view and await store.is_banned(user_id):
        lines.append("🚫 <b>Banned</b>")
    if not expiry and not admin_view and (reason := await store.limit_reason(user_id)):
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
    if await client.store.is_banned(user.id):
        await message.reply_text(BANNED_TEXT)
        return
    if not await in_channel(client, user.id):
        await message.reply_text(
            f"🔒 <b>One quick step</b>\n{LINE}\nJoin our <a href=\"{client.cfg.CHANNEL_LINK}\">updates channel</a> to use this bot, then tap the button below.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I've joined", "chk")]]),
            disable_web_page_preview=True)
        return
    if message.chat.type == enums.ChatType.PRIVATE:
        is_new = await client.store.add_user(user.id)
        await send_welcome(client, user, "https://telegra.ph/file/0351e7d492b896af35c1f.png")
        if is_new:
            await celebrate(client, user.id, "🎉 <b>You're in!</b> Your first downloads are on us.", PARTY)
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
        await client.store.add_user(cb.from_user.id)
        await send_welcome(client, cb.from_user, "https://telegra.ph/file/6aa7c012295bbf509aaa7.png")


async def menu_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    user = cb.from_user
    if cb.data == "START_BUTTON":
        await show(cb, client.start_text(user.mention(style=HTML)), client.start_kb())
    elif cb.data == "COMMAND_BUTTON":
        await show(cb, CMD_TEXT, GOBACK)
    elif cb.data == "PREMIUM_BUTTON":
        await show(cb, client.premium_text(), client.pay_kb())
    elif cb.data == "ACCOUNT_BUTTON":
        await show(cb, await account_text(client, user.id, html.escape(user.first_name or "You")), GOBACK)
    elif cb.data == "HISTORY_BUTTON":
        await show(cb, await history_text(client, user.id), GOBACK)
    await cb.answer()


async def help_cmd(_: AllSaveBot, message: Message) -> None:
    await message.reply_text(f"{HELP_LINKS.format(line=LINE)}\n\n{CMD_TEXT}", disable_web_page_preview=True)


async def premium_cmd(client: AllSaveBot, message: Message) -> None:
    await message.reply_text(client.premium_text(), reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("💳 Pay now", url=client.cfg.DEV_LINK)]]))


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


async def admin_cmd(_: AllSaveBot, message: Message) -> None:
    await message.reply_text(f"🛠 <b>Admin panel</b>\n{LINE}\nChoose a section:", reply_markup=InlineKeyboardMarkup(ADMIN_KB))


async def admin_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    if not client.is_admin(cb.from_user.id):
        await cb.answer("Admins only.", show_alert=True)
        return
    section = cb.data.split(":", 1)[1]
    text = {"help": ADMIN_HELP}.get(section)
    if section == "stats":
        text = await stats_text(client)
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

    premium = await client.store.is_premium(user_id)
    if not premium and (reason := await client.store.limit_reason(user_id)):
        await edit(status, f"⏳ <b>Almost there</b>\n{LINE}\n{html.escape(reason)}", InlineKeyboardMarkup(P_BUTTON))
        return
    try:
        info = await client.svc.info(url)
    except APIError as e:
        await edit(status, f"😕 <b>Couldn't get that video</b>\n{LINE}\n{html.escape(e.message)}")
        return

    options = [o.model_dump() for o in info.options]
    token = await client.store.save_pending(user_id, url, info.title, info.platform, options)
    rows = option_rows(options, token)
    meta = " · ".join(x for x in (f"{PLATFORM_ICONS.get(info.platform, '🎬')} {info.platform.title()}",
                                  f"⏱ {fmt_duration(info.duration)}" if info.duration else "") if x)
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
    premium = await client.store.is_premium(user_id)
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
        job = await client.svc.create_job(pending["url"], option["id"])
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
        if not premium:
            await client.store.refund(user_id)
        await edit(msg, f"😕 <b>Upload failed</b>\n{LINE}\nPlease try again.\n\n<i>This one didn't count against your limit.</i>")
        raise
    finally:
        if job_id:
            client.svc.jobs.served(job_id)  # file is deleted on the next sweep
    await client.store.log_download(user_id, pending.get("platform", "unknown"), option["kind"], title, pending["url"])
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


def register(app: AllSaveBot) -> None:
    sudo = filters.user(app.cfg.SUDO)
    admin = {"sub": sub_cmd, "unsub": unsub_cmd, "user": user_cmd, "ban": ban_cmd, "unban": unban_cmd,
             "stats": stats_cmd, "premiumlist": premiumlist_cmd, "admin": admin_cmd, "bcast": bcast_cmd}
    public = {"start": start, "help": help_cmd, "premium": premium_cmd, "me": me_cmd, "history": history_cmd}
    for name, fn in public.items():
        app.add_handler(MessageHandler(fn, filters.command(name)))
    for name, fn in admin.items():
        app.add_handler(MessageHandler(fn, filters.command(name) & sudo))
    app.add_handler(MessageHandler(on_link, filters.text & filters.regex(URL_RE) & ~filters.command([*public, *admin])))
    for fn, pattern in ((chk, "^chk$"), (menu_buttons, "_BUTTON$"), (admin_buttons, "^ADM:"), (on_cancel, r"^cx:"),
                        (on_pick, r"^dl:[0-9a-f]{12}:\d+$")):
        app.add_handler(CallbackQueryHandler(fn, filters.regex(pattern)))
