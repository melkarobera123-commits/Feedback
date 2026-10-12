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

from telegram import Update
from telegram.ext import (
    Application,
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
COOLDOWN_SECONDS = 30

logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

_last_sent: dict[int, float] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! 👋 Send me your feedback and I'll pass it on anonymously.\n\n"
        "Your name, username and ID are never shared. "
        "Just type your message below."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Just send a text message with your feedback. It will be delivered "
        "without any information about who you are."
    )


async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user_id = msg.from_user.id
    text = msg.text.strip()

    if len(text) > MAX_LEN:
        await msg.reply_text(f"Too long. Please keep it under {MAX_LEN} characters.")
        return

    now = time.monotonic()
    wait = COOLDOWN_SECONDS - (now - _last_sent.get(user_id, 0))
    if wait > 0:
        await msg.reply_text(f"Please wait {int(wait) + 1}s before sending another.")
        return
    _last_sent[user_id] = now

    await context.bot.send_message(
        chat_id=FEEDBACK_CHAT_ID,
        text=f"📩 Anonymous feedback:\n\n{text}",
    )
    await msg.reply_text("Thank you! Your feedback was sent anonymously. ✅")


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Please send your feedback as plain text.")


def main() -> None:
    if not BASE_URL:
        raise SystemExit(
            "No public URL found. On Render this is set automatically; "
            "elsewhere set WEBHOOK_URL to your service address."
        )

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_feedback))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, unsupported))

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
