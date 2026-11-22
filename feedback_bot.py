"""
Anonymous feedback bot for Telegram - ONLINE (webhook) version 2, for Render.

NEW in this version:
  1. Every feedback gets a number and a date/time.
  2. Feedback is delivered to the admin group without a reply/response feature.

Environment variables (set on Render, NEVER written in this file):
  BOT_TOKEN          token from @BotFather
  FEEDBACK_CHAT_ID   chat ID of the admin group (e.g. -1001234567890)
  WEBHOOK_SECRET     any long random text (letters/numbers/_/-)
  TZ_OFFSET_HOURS    optional, hours from UTC for the time shown (default 3)

Feedback is forwarded to the admin group. The bot does not support admin
replies to users.
"""
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
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

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_last_sent: dict[int, float] = {}

# ---------- messages people see ----------

WELCOME = (
    "Baga nagaan gara bootii yaada kennuu FOCUS Jimmaa Yuunivarsiitii "
    "Mooraa Guddaa Nagaan dhuftan! 👋\n\n"
    "Yaada Keessan Icciitiin keessan eegamee nuun gahuu dandeessu!\n\n"
    "✍️ Yaada keessan amma barreessaatii ergaa."
)

PROMPT = "✍️ Yaada keessan amma barreessaatii ergaa."

THANKS = (
    "Yaada keessan waan nuuf qooddaniif, Galatoomaa! FOCUS keessan! ✅\n\n"
    "Yaada biraa erguu yoo barbaaddan, qabduu gadii tuqaa."
)

PRIVACY = (
    "🔒 Icciitii\n\n"
    "• Bulchitoonni maqaa, maqaa fayyadamaa, lakkoofsa bilbilaa yookaan ID "
    "keessan gonkaa hin argan.\n"
    "• Bootiin kuusaa ergaalee hin qabu.\n"
    "• Telegram mataan isaa, akkuma boota biroo, bootii kana waliin "
    "haasa'uu keessan ni beeka."
)


def now_text() -> str:
    tz = timezone(timedelta(hours=TZ_OFFSET_HOURS))
    sign = "+" if TZ_OFFSET_HOURS >= 0 else "-"
    stamp = datetime.now(tz).strftime("%d %b %Y, %H:%M")
    return f"{stamp} (GMT{sign}{abs(TZ_OFFSET_HOURS)})"


def build_admin_text(number, text: str) -> str:
    return (
        f"📩 <b>Feedback #{number}</b>\n"
        f"🕒 {html.escape(now_text())}\n\n"
        f"{html.escape(text)}"
    )


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Yaada keessan barreessaatii ergaa. "
        "Icciitiin keessan akkamitti akka eegamu ilaaluuf /privacy fayyadamaa."
    )


async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(PRIVACY)


# ---------- a person writes feedback, bot delivers it to the admins ----------

async def receive_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    text = msg.text.strip()

    if len(text) > MAX_LEN:
        await msg.reply_text(
            f"Ergaan keessan baay'ee dheeraadha. Maaloo qubee {MAX_LEN} gadi ta'e barreessaa."
        )
        return

    user_id = msg.from_user.id
    now = time.monotonic()
    wait = COOLDOWN_SECONDS - (now - _last_sent.get(user_id, 0))
    if COOLDOWN_SECONDS and wait > 0:
        await msg.reply_text(f"Maaloo sekondii {int(wait) + 1} eegaa.")
        return
    _last_sent[user_id] = now

    try:
        # Send first, then use Telegram's message number as the feedback number.
        sent = await context.bot.send_message(
            chat_id=FEEDBACK_CHAT_ID,
            text=build_admin_text("…", text),
            parse_mode=ParseMode.HTML,
        )
        number = sent.message_id
        await context.bot.edit_message_text(
            chat_id=FEEDBACK_CHAT_ID,
            message_id=number,
            text=build_admin_text(number, text),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        await msg.reply_text(
            "Dhiifama, yaada keessan ergu hin dandeenye. Maaloo yeroo booda irra deebi'aa yaalaa."
        )
        return

    again = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✍️ Yaada biraa ergi", callback_data="again")]]
    )
    await msg.reply_text(
        f"{THANKS}\n\n🔢 Lakkoofsa yaada keessanii: #{number}",
        reply_markup=again,
    )


async def send_another(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await context.bot.send_message(chat_id=query.message.chat.id, text=PROMPT)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Maaloo yaada keessan barreeffamaan qofa ergaa.")


def main() -> None:
    if not BASE_URL:
        raise SystemExit(
            "No public URL found. On Render this is set automatically; "
            "elsewhere set WEBHOOK_URL to your service address."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", help_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("privacy", privacy_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(send_another, pattern=r"^again$"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_feedback)
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
