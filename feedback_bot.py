"""
Anonymous feedback bot for Telegram - ONLINE (webhook) version 2, for Render.

NEW in this version:
  1. Every feedback gets a number and a date/time.
  2. The sender picks a category with buttons.
  3. Admins can reply anonymously: in the admin group, tap Reply on a feedback
     message and write the answer. The bot delivers it to the sender without
     showing who the sender or the admin is.

Environment variables (set on Render, NEVER written in this file):
  BOT_TOKEN          token from @BotFather
  FEEDBACK_CHAT_ID   chat ID of the admin group (e.g. -1001234567890)
  WEBHOOK_SECRET     any long random text (letters/numbers/_/-)
  TZ_OFFSET_HOURS    optional, hours from UTC for the time shown (default 3)

How replies work without a database:
  Each feedback message carries a short encrypted code ("ref"). Only this bot
  can read it. It is different for every message, so admins cannot tell that
  two messages came from the same person. Nothing is stored on disk.
"""
import base64
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
FEEDBACK_CHAT_ID = int(os.environ["FEEDBACK_CHAT_ID"])
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
PORT = int(os.environ.get("PORT", "10000"))
BASE_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
TZ_OFFSET_HOURS = int(os.environ.get("TZ_OFFSET_HOURS", "3"))

MAX_LEN = 3000
COOLDOWN_SECONDS = 0  # 0 = no waiting between messages

CATEGORIES = {
    "suggestion": "💡 Suggestion",
    "complaint": "⚠️ Complaint",
    "question": "❓ Question",
    "other": "📝 Other",
}

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_last_sent: dict[int, float] = {}

# ---------- secret code (ref) that lets the bot find the sender again ----------

_KEY = hashlib.sha256(("feedback-ref-key|" + BOT_TOKEN).encode()).digest()
REF_RE = re.compile(r"ref:\s*([A-Za-z0-9_-]{22})")
NUM_RE = re.compile(r"Feedback #(\d+)")


def _pad(nonce: bytes) -> bytes:
    return hmac.new(_KEY, b"enc" + nonce, hashlib.sha256).digest()[:6]


def _tag(nonce: bytes, cipher: bytes) -> bytes:
    return hmac.new(_KEY, b"tag" + nonce + cipher, hashlib.sha256).digest()[:6]


def make_ref(user_id: int) -> str:
    nonce = secrets.token_bytes(4)
    cipher = bytes(a ^ b for a, b in zip(user_id.to_bytes(6, "big"), _pad(nonce)))
    raw = nonce + cipher + _tag(nonce, cipher)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_ref(ref: str):
    try:
        raw = base64.urlsafe_b64decode(ref + "=" * (-len(ref) % 4))
        if len(raw) != 16:
            return None
        nonce, cipher, tag = raw[:4], raw[4:10], raw[10:]
        if not hmac.compare_digest(tag, _tag(nonce, cipher)):
            return None
        plain = bytes(a ^ b for a, b in zip(cipher, _pad(nonce)))
        return int.from_bytes(plain, "big")
    except Exception:
        return None


# ---------- messages people see ----------

WELCOME = (
    "Hi! 👋 Send me your feedback and I'll pass it on anonymously.\n\n"
    "Your name, username and ID are never shown to the admins.\n"
    "Just type your message below, then choose a category."
)

PRIVACY = (
    "🔒 Privacy\n\n"
    "• Admins never see your name, username, phone number or ID.\n"
    "• To let admins answer you, each message carries a secret code that only "
    "this bot can read. It is different for every message.\n"
    "• The bot does not keep a database of messages.\n"
    "• Telegram itself still knows you are talking to this bot, as with any bot."
)


def now_text() -> str:
    tz = timezone(timedelta(hours=TZ_OFFSET_HOURS))
    sign = "+" if TZ_OFFSET_HOURS >= 0 else "-"
    stamp = datetime.now(tz).strftime("%d %b %Y, %H:%M")
    return f"{stamp} (GMT{sign}{abs(TZ_OFFSET_HOURS)})"


def build_admin_text(number, category: str, text: str, ref: str) -> str:
    return (
        f"📩 <b>Feedback #{number}</b>\n"
        f"🏷 {html.escape(category)}\n"
        f"🕒 {html.escape(now_text())}\n\n"
        f"{html.escape(text)}\n\n"
        f"↩️ <i>Reply to this message to answer anonymously.</i>\n"
        f"<code>ref: {ref}</code>"
    )


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send a text message with your feedback, then tap a category. "
        "If the admins answer, you will get their reply here.\n\n"
        "Use /privacy to see how your privacy is protected."
    )


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(PRIVACY)


# ---------- step 1: person writes feedback, bot asks for a category ----------

async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    text = msg.text.strip()

    if len(text) > MAX_LEN:
        await msg.reply_text(f"Too long. Please keep it under {MAX_LEN} characters.")
        return

    buttons = [
        [
            InlineKeyboardButton(CATEGORIES["suggestion"], callback_data="cat:suggestion"),
            InlineKeyboardButton(CATEGORIES["complaint"], callback_data="cat:complaint"),
        ],
        [
            InlineKeyboardButton(CATEGORIES["question"], callback_data="cat:question"),
            InlineKeyboardButton(CATEGORIES["other"], callback_data="cat:other"),
        ],
    ]
    # This reply is attached to the person's message, so when they tap a
    # button the bot can read their original text again. No storage needed.
    await msg.reply_text(
        "Thanks! Which category fits best?",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ---------- step 2: person taps a category, bot delivers to the admins ----------

async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    key = (query.data or "").split(":", 1)[-1]
    category = CATEGORIES.get(key)
    original = getattr(query.message, "reply_to_message", None)

    if not category or not original or not original.text:
        await query.edit_message_text(
            "Sorry, I lost your message. Please send your feedback again."
        )
        return

    user_id = query.from_user.id
    now = time.monotonic()
    wait = COOLDOWN_SECONDS - (now - _last_sent.get(user_id, 0))
    if COOLDOWN_SECONDS and wait > 0:
        await query.answer(f"Please wait {int(wait) + 1}s.", show_alert=True)
        return
    _last_sent[user_id] = now

    text = original.text.strip()
    ref = make_ref(query.message.chat.id)

    try:
        # Send first, then use Telegram's message number as the feedback number.
        sent = await context.bot.send_message(
            chat_id=FEEDBACK_CHAT_ID,
            text=build_admin_text("…", category, text, ref),
            parse_mode=ParseMode.HTML,
        )
        number = sent.message_id
        await context.bot.edit_message_text(
            chat_id=FEEDBACK_CHAT_ID,
            message_id=number,
            text=build_admin_text(number, category, text, ref),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        await query.edit_message_text(
            "Sorry, I could not deliver your feedback. Please try again later."
        )
        return

    await query.edit_message_text(
        f"✅ Thank you! Your feedback #{number} was sent anonymously.\n"
        "If the admins reply, you will get the answer here."
    )


# ---------- step 3: an admin replies in the group, bot forwards anonymously ----------

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    target = msg.reply_to_message
    if not target or not target.from_user or target.from_user.id != context.bot.id:
        return

    found = REF_RE.search(target.text or "")
    if not found:
        return  # not a feedback message (for example a confirmation)

    user_id = read_ref(found.group(1))
    if user_id is None:
        await msg.reply_text("⚠️ I could not read the code of this feedback.")
        return

    number = NUM_RE.search(target.text or "")
    label = f" #{number.group(1)}" if number else ""

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"💬 Reply from the admins to your feedback{label}:\n\n{msg.text}",
        )
        await msg.reply_text("✅ Reply sent anonymously.")
    except Forbidden:
        await msg.reply_text("⚠️ This person has blocked the bot, so I could not deliver it.")
    except TelegramError:
        await msg.reply_text("⚠️ Could not deliver the reply. Please try again.")


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Please send your feedback as plain text.")


def main() -> None:
    if not BASE_URL:
        raise SystemExit(
            "No public URL found. On Render this is set automatically; "
            "elsewhere set WEBHOOK_URL to your service address."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    # Admin replies first, so they are never mistaken for new feedback.
    app.add_handler(
        MessageHandler(
            filters.Chat(FEEDBACK_CHAT_ID) & filters.REPLY & filters.TEXT & ~filters.COMMAND,
            admin_reply,
        )
    )
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", help_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("privacy", privacy_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(category_chosen, pattern=r"^cat:"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, ask_category)
    )
    app.add_handler(
        MessageHandler(~filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unsupported)
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=f"{BASE_URL.rstrip('/')}/telegram",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
