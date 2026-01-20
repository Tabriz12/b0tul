import os
from enum import IntEnum

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import settings
from telegram_bot.helpers.logger import setup_logger
from telegram_bot.llm.my_ollama import OllamaHandler

ollama_client = OllamaHandler()

logger = setup_logger("main_bot")


class States(IntEnum):
    GENERIC_MESSAGE = 1


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Handle /start command and greet the user."""
    user = update.effective_user
    name = user.first_name if user and user.first_name else "there"
    await update.message.reply_text(f"Hello, {name}! 👋 Welcome to the bot.")

    return States.GENERIC_MESSAGE


async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to any text message with a friendly greeting."""

    if update.message and update.message.text:
        try:
            response = ollama_client.send_message(update.message.text)

            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Error in reply handler: {e}")
            await update.message.reply_text(
                "Sorry, something went wrong while processing your message."
            )


# async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """
#     generate a new chat session with uuid

#     :param update: Description
#     :type update: Update
#     :param context: Description
#     :type context: ContextTypes.DEFAULT_TYPE


#     """

#     await update.message.reply_text("Starting a new chat session...")

#     topic_id = str(uuid4())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text(
        "Bye! Hope to talk to you again soon.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


def get_token() -> str | None:
    token = settings.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def main() -> None:
    token = get_token()
    if not token:
        logger.error(
            """No Telegram bot token found. Set DYNACONF_TELEGRAM_BOT_TOKEN or
                TELEGRAM_BOT_TOKEN."""
        )
        return

    app = Application.builder().token(token).build()

    logger.info("Starting bot...")

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],  # ty:ignore[invalid-argument-type]
        states={
            States.GENERIC_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reply)
            ]
        },  # ty:ignore[invalid-argument-type]
        fallbacks=[CommandHandler("cancel", cancel)],  # ty:ignore[invalid-argument-type]
    )

    app.add_handler(conv_handler)

    # Handle the case when a user sends /start but they're not in a conversation
    app.add_handler(CommandHandler("start", start))

    app.run_polling()


if __name__ == "__main__":
    main()
