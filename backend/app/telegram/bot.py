"""All Save Bot, running inside the backend process and using the same job system as the web API."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

from pyrogram import Client, enums, errors, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..errors import APIError
from ..service import AllSave
from .config import TelegramConfig
from .db import Store

log = logging.getLogger("allsave.telegram")
HTML = enums.ParseMode.HTML
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
Kb = list[list[InlineKeyboardButton]]


class AllSaveBot(Client):
    def __init__(self, cfg: TelegramConfig, svc: AllSave) -> None:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        super().__init__("all_save", api_id=cfg.API_ID, api_hash=cfg.API_HASH, bot_token=cfg.BOT_TOKEN,
                         workdir=str(cfg.DATA_DIR), workers=32)
        self.cfg, self.svc, self.store = cfg, svc, Store(cfg)
        self.link = f"https://t.me/{cfg.BOT_USERNAME}"
        register(self)

    async def shutdown(self) -> None:
        await self.stop()
        await self.store.close()

    # keyboards and texts depend on config
    def start_kb(self) -> Kb:
        return [[InlineKeyboardButton("📖 Commands", callback_data="COMMAND_BUTTON"),
                 InlineKeyboardButton("👨‍💻 About me", callback_data="ABOUT_BUTTON")],
                [InlineKeyboardButton("➕ Add me to your Chat ➕", url=f"{self.link}?startgroup")]]

    def share_kb(self) -> Kb:
        return [[InlineKeyboardButton("Share ☘️", url="https://t.me/share/url?url=Check%20out%20this%20awesome%20bot%20for%20downloading%20videos%21%20%F0%9F%94%A5%0A%0AAll%20Save%20Bot%20%E2%98%98%EF%B8%8F%20%3A%20%20%40" + self.cfg.BOT_USERNAME)]]

    def start_text(self, mention: str) -> str:
        return START_TEXT.format(mention=mention, link=self.link)

    def premium_text(self) -> str:
        c = self.cfg
        return P_TEXT.format(limit=c.FREE_DAILY_LIMIT, cooldown=c.COOLDOWN_MINUTES, days=c.PREMIUM_DAYS)


GOBACK_1_BUTTON = [[InlineKeyboardButton("🔙 Go Back", callback_data="START_BUTTON")]]
P_BUTTON = [[InlineKeyboardButton("💳 Get Premium", callback_data="PREMIUM_BUTTON")]]

START_TEXT = """**👋👋 Hello there,** {mention}

I'm **[All Save Bot 🍀]({link})**, at your service! 💪

Send me a **TikTok, YouTube, Instagram or Facebook** link and pick a quality — or grab just the MP3 audio. TikTok videos come without watermark when available. 🎥🎵

I'm group-friendly too! Use me in your groups to share and save videos with your friends. 🤝👥"""
CMD_TEXT = """Here are the commands available for this bot:

👇🤖 Bot Commands 👇

<code>/start</code>: Start the bot.
<code>/help</code>: How to use the bot. 🎊
<code>/premium</code>: Unlimited downloads with no waiting. 💳
<code>/stats</code>: Bot statistics (admins only).

Just send a link to start downloading! 💫🤖"""
ABOUT_TEXT = """👋👋 I'm **All Save Bot ☘️**. I download videos and audio from **TikTok, YouTube, Instagram and Facebook**.

Please download only content you have the right to save, and respect creators and platform terms.

Have an idea or found a bug? Reach out to the developer. 🌟🤖"""
P_TEXT = """Dear User🧑🏻‍💻,

Free users get **{limit} downloads per day** with a {cooldown}-minute wait between them.
Premium removes the limits for {days} days.

⮞ Premium Price: 3$
Payment Methods: PayPal, Crypto & Bank Transfer (Sri Lankan Only) 🏦💸

Tap **Pay Now** to contact the developer. 💫🤖"""
HELP_TEXT = """Hello {mention} 👋👋,
I am **All Save Bot ☘️**. Send me a link like:

<code>https://www.tiktok.com/@user/video/7123456789
https://youtu.be/dQw4w9WgXcQ
https://www.instagram.com/reel/Cabc123/
https://www.facebook.com/watch/?v=123456789</code>

Then choose a video quality or MP3 audio. __Send me a video link__"""


def _size(n: int | None) -> str:
    return f" · {n / 1048576:.0f} MB" if n and n >= 1048576 else ""


def _duration(s: int | None) -> str:
    return f"{s // 60}:{s % 60:02d}" if s else ""


async def edit(msg: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        if msg.photo:
            await msg.edit_caption(text, parse_mode=HTML, reply_markup=markup)
        else:
            await msg.edit_text(text, parse_mode=HTML, reply_markup=markup, disable_web_page_preview=True)
    except (errors.MessageNotModified, errors.FloodWait):
        pass


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


async def send_welcome(client: AllSaveBot, user_id: int, mention: str, photo: str) -> None:
    text, markup = client.start_text(mention), InlineKeyboardMarkup(client.start_kb())
    try:
        await client.send_photo(user_id, photo=photo, caption=text, reply_markup=markup)
    except errors.RPCError:  # Telegram could not fetch the image; the user still gets a reply
        log.exception("Welcome photo failed")
        await client.send_message(user_id, text, reply_markup=markup)


# ---- basic commands -------------------------------------------------------------------------------------------

async def start(client: AllSaveBot, message: Message) -> None:
    if not message.from_user:
        return
    log.info("/start from %s in %s", message.from_user.id, message.chat.type)
    if not await in_channel(client, message.from_user.id):
        await message.reply_text(
            f"**⚠️Access Denied!⚠️\n\nTo use me, please join the Bots Updates Channel first: [Join Channel]({client.cfg.CHANNEL_LINK}).**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🍀 Check Again 🍀", "chk")]]))
        return
    if message.chat.type == enums.ChatType.PRIVATE:
        await client.store.add_user(message.from_user.id)
        await send_welcome(client, message.from_user.id, message.from_user.mention,
                           "https://telegra.ph/file/0351e7d492b896af35c1f.png")
    else:
        await client.store.add_group(message.chat.id)
        await message.reply_text(
            f"**👋👋 Hello {html.escape(message.from_user.first_name or '')}!\nLet's take this to the inbox for a more private chat. 📩🤫**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Come IB😏", url=f"{client.link}?start=start")]]))


async def chk(client: AllSaveBot, cb: CallbackQuery) -> None:
    if not await in_channel(client, cb.from_user.id):
        await cb.answer("🙅‍♂️ Please join the channel first 😒", show_alert=True)
        return
    if cb.message.chat.type == enums.ChatType.PRIVATE:
        await client.store.add_user(cb.from_user.id)
        await send_welcome(client, cb.from_user.id, cb.from_user.mention,
                           "https://telegra.ph/file/6aa7c012295bbf509aaa7.png")


async def menu_buttons(client: AllSaveBot, cb: CallbackQuery) -> None:
    pages: dict[str, tuple[str, Kb]] = {
        "START_BUTTON": (client.start_text(cb.from_user.mention), client.start_kb()),
        "COMMAND_BUTTON": (CMD_TEXT, GOBACK_1_BUTTON),
        "ABOUT_BUTTON": (ABOUT_TEXT, [[InlineKeyboardButton("👨‍💻 Developer", url=client.cfg.DEV_LINK),
                                       InlineKeyboardButton("🔙 Go Back", callback_data="START_BUTTON")]]),
        "PREMIUM_BUTTON": (client.premium_text(), [[InlineKeyboardButton("💳 Pay Now", url=client.cfg.DEV_LINK)]]),
    }
    if page := pages.get(cb.data):
        text, buttons = page
        try:
            if cb.message.photo:
                await cb.edit_message_caption(text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await cb.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        except errors.MessageNotModified:
            pass
    await cb.answer()


async def help_cmd(_: AllSaveBot, message: Message) -> None:
    mention = message.from_user.mention if message.from_user else "there"
    await message.reply_text(HELP_TEXT.format(mention=mention), disable_web_page_preview=True)


async def premium_cmd(client: AllSaveBot, message: Message) -> None:
    await message.reply_text(client.premium_text(), reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("💳 Pay Now", url=client.cfg.DEV_LINK)]]))


# ---- admin commands -------------------------------------------------------------------------------------------

async def sub_cmd(client: AllSaveBot, message: Message) -> None:
    args = message.command[1:]
    try:
        user_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else client.cfg.PREMIUM_DAYS
    except (IndexError, ValueError):
        await message.reply_text("Usage: <code>/sub user_id [days]</code>", parse_mode=HTML)
        return
    expiry = await client.store.make_premium(user_id, days)
    await message.reply_text(f"{user_id} is premium until {expiry:%Y-%m-%d %H:%M} UTC.")
    try:
        await client.send_message(user_id, "Congratulations! You are now a premium user. 🎉")
    except errors.RPCError:
        pass


async def stats_cmd(client: AllSaveBot, message: Message) -> None:
    users, groups, premium = await client.store.counts()
    await message.reply_text(f"🍀 Chats Stats 🍀\n🙋‍♂️ Users : `{users}`\n👥 Groups : `{groups}`\n"
                             f"💳 Active premium : `{premium}`\n🚧 Total users & groups : `{users + groups}`")


async def bcast_cmd(client: AllSaveBot, m: Message) -> None:
    if not m.reply_to_message:
        await m.reply_text("Reply to the message you want to broadcast with /bcast.")
        return
    status = await m.reply_text("`⚡️ Processing...`")
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
    await status.edit(f"✅Successful to `{success}` users.\n❌ Failed to `{failed}` users.\n"
                      f"👾 Found `{blocked}` Blocked users \n👻 Found `{deactivated}` Deactivated users.")


# ---- downloads ------------------------------------------------------------------------------------------------

async def on_link(client: AllSaveBot, message: Message) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    url = URL_RE.search(message.text).group(0)  # type: ignore[union-attr]
    status = await message.reply_text("<code>Processing.....</code>", parse_mode=HTML)

    if not await client.store.is_premium(user_id) and (reason := await client.store.limit_reason(user_id)):
        await edit(status, f"<code>{html.escape(reason)}</code>", InlineKeyboardMarkup(P_BUTTON))
        return
    try:
        info = await client.svc.info(url)
    except APIError as e:
        await edit(status, f"❌ {html.escape(e.message)}")
        return

    options = [o.model_dump() for o in info.options]
    token = await client.store.save_pending(user_id, url, info.title, options)
    rows = [[InlineKeyboardButton(f"{'🎬' if o['kind'] == 'video' else '🎵'} {o['label']}{_size(o['size'])}",
                                  callback_data=f"dl:{token}:{i}")] for i, o in enumerate(options)]
    rows.append([InlineKeyboardButton("✖ Cancel", callback_data=f"cx:{token}")])
    duration = _duration(info.duration)
    text = (f"<b>{html.escape(info.title)}</b>\n{duration and f'⏱ {duration}  '}<i>{info.platform.title()}</i>"
            "\n\n<code>Please select a format:</code>")
    if info.thumbnail:
        try:
            await message.reply_photo(info.thumbnail, caption=text, parse_mode=HTML,
                                      reply_markup=InlineKeyboardMarkup(rows))
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
    pending = await client.store.get_pending(token)
    if not pending:
        await cb.answer("This request expired. Send the link again.", show_alert=True)
        return
    if pending["user_id"] != user_id:
        await cb.answer("This isn't your request. Send your own link.", show_alert=True)
        return
    premium = await client.store.is_premium(user_id)
    if not premium and (reason := await client.store.consume(user_id)):
        await cb.answer(reason, show_alert=True)
        return
    if not await client.store.take_pending(token, user_id):  # double tap: already started
        if not premium:
            await client.store.refund(user_id)
        await cb.answer()
        return
    await cb.answer()

    option = pending["options"][int(idx)]
    title, msg = pending["title"], cb.message
    last_shown: tuple[str, int] | None = None

    async def on_update(state: str, progress: int) -> None:
        nonlocal last_shown
        shown = (state, progress // 10)
        if shown != last_shown:
            last_shown = shown
            label = {"queued": "⏳ Waiting in queue…", "downloading": f"⬇️ Downloading… {progress}%",
                     "processing": "⚙️ Processing…"}.get(state, state)
            await edit(msg, f"<b>{html.escape(title)}</b>\n\n<code>{label}</code>")

    job_id = None
    try:
        job = await client.svc.create_job(pending["url"], option["id"])
        job_id = job.id
        job = await client.svc.wait(job.id, on_update)
        await edit(msg, f"<b>{html.escape(title)}</b>\n\n<code>📤 Uploading… this can take a moment for big files.</code>")
        await upload(client, cb, job.file, job.filename or job.file.name, option, title)  # type: ignore[arg-type]
    except APIError as e:
        if not premium:
            await client.store.refund(user_id)
        await edit(msg, f"❌ {html.escape(e.message)}")
        return
    except Exception:
        if not premium:
            await client.store.refund(user_id)
        await edit(msg, "❌ Upload failed. Please try again.")
        raise
    finally:
        if job_id:
            client.svc.jobs.served(job_id)  # file is deleted on the next sweep
    await msg.delete()


async def upload(client: AllSaveBot, cb: CallbackQuery, path: Any, file_name: str, option: dict[str, Any],
                 title: str) -> None:
    chat_id = cb.message.chat.id
    caption = (f"<b>Here Is your Requested {'Audio' if option['kind'] == 'audio' else 'Video'}👆</b>\n\n"
               f"<code>{html.escape(title[:800])}</code>\n\n🔗 Requestor : <tg-spoiler>{cb.from_user.mention(style=HTML)}</tg-spoiler>\n"
               f"🚀 Downloaded via: <a href=\"{client.link}\">All Save Bot ☘️</a>")
    kwargs: dict[str, Any] = {"caption": caption, "parse_mode": HTML, "file_name": file_name,
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
    commands = ["start", "help", "premium", "sub", "stats", "bcast"]
    for handler in (
        MessageHandler(start, filters.command("start")),
        MessageHandler(help_cmd, filters.command("help")),
        MessageHandler(premium_cmd, filters.command("premium")),
        MessageHandler(sub_cmd, filters.command("sub") & sudo),
        MessageHandler(stats_cmd, filters.command("stats") & sudo),
        MessageHandler(bcast_cmd, filters.command("bcast") & sudo),
        MessageHandler(on_link, filters.text & filters.regex(URL_RE) & ~filters.command(commands)),
        CallbackQueryHandler(chk, filters.regex("^chk$")),
        CallbackQueryHandler(menu_buttons, filters.regex("_BUTTON$")),
        CallbackQueryHandler(on_cancel, filters.regex(r"^cx:")),
        CallbackQueryHandler(on_pick, filters.regex(r"^dl:[0-9a-f]{12}:\d+$")),
    ):
        app.add_handler(handler)
