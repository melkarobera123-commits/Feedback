"""
Anonymous feedback bot for Telegram - ONLINE (webhook) version for Render.

Environment variables to set on Render (never put them in this file):
  BOT_TOKEN          token from @BotFather
  FEEDBACK_CHAT_ID   the chat ID where feedback should arrive
  WEBHOOK_SECRET     any long random text (letters/numbers), protects the webhook

Render provides PORT and RENDER_EXTERNAL_URL automatically.

Anonymity: only the message TEXT is re-sent. No name, username or ID is
included and nothing is stored on disk.
"""
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

MAX_LEN = 3000
COOLDOWN_SECONDS = 0

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_last_sent: dict[int, float] = {}

WELCOME_TEXT = (
    "Baga nagaan gara bootii yaada kennuu FOCUS Jimmaa Yuunivarsiitii "
    "Mooraa Guddaa Nagaan dhuftan! 👋\n\n"
    "Yaada Keessan Icciitiin keessan eegamee nuun gahuu dandeessu!\n\n"
    "✍️ Yaada keessan amma barreessaatii ergaa."
)

THANKS_TEXT = (
    "Yaada keessan waan nuuf qooddaniif, Galatoomaa! FOCUS keessan! ✅\n\n"
    "Yaada biraa erguu yoo barbaaddan, qabduu gadii tuqaa."
)

PROMPT_TEXT = "✍️ Yaada keessan amma barreessaatii ergaa."

SEND_BUTTON = InlineKeyboardMarkup(
    [[InlineKeyboardButton("✍️ Yaada ergi", callback_data="send")]]
)
ANOTHER_BUTTON = InlineKeyboardMarkup(
    [[InlineKeyboardButton("➕ Yaada biraa ergi", callback_data="send")]]
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT, reply_markup=SEND_BUTTON)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Yaada keessan barreeffama (text) ta'ee ergaa. "
        "Maqaan, username fi ID keessan hin ergamu.\n\n"
        "Deebi'ee jalqabuuf /start tuqaa.",
        reply_markup=SEND_BUTTON,
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(PROMPT_TEXT)


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_id = msg.from_user.id
    text = msg.text.strip()

    if len(text) > MAX_LEN:
        await msg.reply_text(
            f"Yaadni keessan dheeraa dha. Mee dubbii {MAX_LEN} gadi qabaa."
        )
        return

    now = time.monotonic()
    wait = COOLDOWN_SECONDS - (now - _last_sent.get(user_id, 0))
    if wait > 0:
        await msg.reply_text(
            f"Mee sekondii {int(wait) + 1} eegaa, achiin booda deebisaa ergaa.",
            reply_markup=ANOTHER_BUTTON,
        )
        return
    _last_sent[user_id] = now

    await context.bot.send_message(
        chat_id=FEEDBACK_CHAT_ID,
        text=f"📩 Anonymous feedback:\n\n{text}",
    )
    await msg.reply_text(THANKS_TEXT, reply_markup=ANOTHER_BUTTON)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Mee yaada keessan barreeffama (text) ta'ee ergaa.",
        reply_markup=SEND_BUTTON,
    )


def main() -> None:
    if not BASE_URL:
        raise SystemExit(
            "No public URL found. On Render this is set automatically; "
            "elsewhere set WEBHOOK_URL to your service address."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_button, pattern="^send$"))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_feedback,
        )
    )
    app.add_handler(
        MessageHandler(
            ~filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            unsupported,
        )
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=f"{BASE_URL.rstrip('/')}/telegram",
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
