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
from telegram.request import HTTPXRequest

from config import settings
from telegram_bot.helpers.answers_data import get_all_answers, set_answer
from telegram_bot.helpers.job_data import (
    delete_job_draft,
    get_job_draft,
    save_job_draft,
    update_job_draft,
)
from telegram_bot.helpers.logger import setup_logger
from telegram_bot.helpers.user_data import get_user_cv, set_user_cv
from telegram_bot.llm.my_ollama import OllamaHandler
from telegram_bot.parsers.djinni import DjinniParser
from telegram_bot.parsers.models import ApplicationQuestion

ollama_client = OllamaHandler()

logger = setup_logger("main_bot")


class States(IntEnum):
    GENERIC_MESSAGE = 1
    SET_CV = 2
    COVER_LETTER = 3
    EDIT_COVER_LETTER = 4


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


async def set_cv_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Ask the user to provide their CV text."""
    await update.message.reply_text(
        "Please paste your CV text. I'll store it for future cover letters."
    )
    return States.SET_CV


async def set_cv_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Save the user's CV and return to generic state."""
    if update.message and update.message.text and update.effective_user:
        set_user_cv(update.effective_user.id, update.message.text)
        await update.message.reply_text("Thanks! Your CV is saved.")
    return States.GENERIC_MESSAGE


async def cover_letter_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Start cover letter generation by requesting job description text."""
    if not update.effective_user:
        return States.GENERIC_MESSAGE

    cv = get_user_cv(update.effective_user.id)
    if not cv:
        await update.message.reply_text(
            "I don't have your CV yet. Use /set_cv to add it first."
        )
        return States.GENERIC_MESSAGE

    await update.message.reply_text(
        "Send the job description (or a pasted listing) and I'll draft a cover letter."
    )
    return States.COVER_LETTER


async def cover_letter_generate(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Generate a cover letter based on the saved CV and job description."""
    if not (update.message and update.message.text and update.effective_user):
        return States.GENERIC_MESSAGE

    cv = get_user_cv(update.effective_user.id)
    if not cv:
        await update.message.reply_text(
            "I don't have your CV yet. Use /set_cv to add it first."
        )
        return States.GENERIC_MESSAGE

    try:
        cover_letter = ollama_client.generate_cover_letter(cv, update.message.text)
        await update.message.reply_text(cover_letter)
    except Exception as e:
        logger.error(f"Error generating cover letter: {e}")
        await update.message.reply_text(
            "Sorry, something went wrong while generating the cover letter."
        )

    return States.GENERIC_MESSAGE


def _preview_text(text: str, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_keywords(args: list[str]) -> list[str]:
    if not args:
        return []
    keywords: list[str] = []
    for arg in args:
        parts = [part.strip() for part in arg.split(",") if part.strip()]
        keywords.extend(parts)
    return keywords


def _extract_page_limit_keywords(args: list[str]) -> tuple[int, int, list[str]]:
    page_num = 1
    limit = 3
    keywords: list[str] = []

    if not args:
        return page_num, limit, keywords

    # Supports:
    # /djinni_jobs 2 5 python
    # /djinni_jobs page=2 limit=5 python
    # /djinni_jobs --page 2 --limit 5 python
    remaining: list[str] = []
    idx = 0
    while idx < len(args):
        arg = args[idx]
        lowered = arg.lower()

        if lowered.startswith("page="):
            parsed = _parse_int(arg.split("=", 1)[1].strip())
            if parsed is not None:
                page_num = parsed
            idx += 1
            continue

        if lowered.startswith("limit="):
            parsed = _parse_int(arg.split("=", 1)[1].strip())
            if parsed is not None:
                limit = parsed
            idx += 1
            continue

        if lowered in {"--page", "-p"} and idx + 1 < len(args):
            parsed = _parse_int(args[idx + 1])
            if parsed is not None:
                page_num = parsed
                idx += 2
                continue

        if lowered in {"--limit", "-l"} and idx + 1 < len(args):
            parsed = _parse_int(args[idx + 1])
            if parsed is not None:
                limit = parsed
                idx += 2
                continue

        remaining.append(arg)
        idx += 1

    if not remaining:
        return page_num, limit, keywords

    first = _parse_int(remaining[0])
    if first is None:
        return page_num, limit, _parse_keywords(remaining)
    page_num = first

    if len(remaining) > 1:
        second = _parse_int(remaining[1])
        if second is None:
            return page_num, limit, _parse_keywords(remaining[1:])
        limit = second

    if len(remaining) > 2:
        keywords = _parse_keywords(remaining[2:])

    return page_num, limit, keywords


def _matches_keywords(description: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lowered = description.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _format_questions(questions: list[ApplicationQuestion]) -> str:
    lines: list[str] = []
    for idx, question in enumerate(questions, start=1):
        text = question.text.strip()
        options = question.options
        options_text = ""
        if options:
            options_text = " Options: " + ", ".join([str(o) for o in options])
        lines.append(f"{idx}. {text}{options_text}")
    return "\n".join(lines)


async def djinni_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Fetch jobs from Djinni and prepare cover letters for approval."""
    logger.info("Handling /djinni_jobs command")
    if not update.effective_user:
        return States.GENERIC_MESSAGE

    cv = get_user_cv(update.effective_user.id)
    if not cv:
        await update.message.reply_text(
            "I don't have your CV yet. Use /set_cv to add it first."
        )
        return States.GENERIC_MESSAGE

    args = context.args or []
    page_num, limit, keywords = _extract_page_limit_keywords(args)

    await update.message.reply_text("Fetching jobs and drafting cover letters...")

    try:
        parser = DjinniParser(
            email=settings.get("DJINNI_EMAIL"),
            password=settings.get("DJINNI_PASSWORD"),
        )
        jobs = await parser.collect_jobs(page_num=page_num, limit=limit)
    except Exception as e:
        logger.error(f"Error fetching Djinni jobs: {e}")
        await update.message.reply_text("Failed to fetch jobs from Djinni.")
        return States.GENERIC_MESSAGE

    filtered_jobs = [
        job for job in jobs if _matches_keywords(job["description"], keywords)
    ]

    if not filtered_jobs:
        await update.message.reply_text(f"No matching jobs found on page {page_num}.")
        return States.GENERIC_MESSAGE

    for job in filtered_jobs:
        job_id = str(job["job_id"])
        description = str(job["description"])
        page_num = int(job["page_num"])
        cover_letter = ollama_client.generate_cover_letter(cv, description)

        save_job_draft(
            update.effective_user.id,
            job_id,
            {
                "description": description,
                "cover_letter": cover_letter,
                "page_num": page_num,
                "approved": False,
                "ready": False,
                "pending_questions": {},
            },
        )

        await update.message.reply_text(
            f"Job ID: {job_id}\n\n"
            f"Description preview:\n{_preview_text(description)}\n\n"
            f"Draft cover letter:\n{cover_letter}\n\n"
            f"Approve with: /approve {job_id}\n"
            f"Edit with: /edit {job_id}\n"
            f"Prepare with: /apply {job_id}\n"
            f"Confirm with: /confirm {job_id}\n"
            f"Discard with: /skip {job_id}"
        )

    return States.GENERIC_MESSAGE


async def apply_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Prepare a Djinni application and check for unanswered questions."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /apply <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    if not job.get("approved"):
        await update.message.reply_text(
            "This draft isn't approved yet. Use /approve <job_id> or /edit <job_id>."
        )
        return States.GENERIC_MESSAGE

    await update.message.reply_text(f"Preparing application for job {job_id}...")

    try:
        from telegram_bot.parsers.djinni import DjinniParser

        parser = DjinniParser(
            email=settings.get("DJINNI_EMAIL"),
            password=settings.get("DJINNI_PASSWORD"),
        )
        answers = get_all_answers()
        unanswered = await parser.prepare_job_application(
            job_id=job_id,
            message=job["cover_letter"],
            answers=answers,
            page_num=int(job.get("page_num", 1)),
        )

        if unanswered:
            pending = {
                f"q{idx}": question.model_dump()
                for idx, question in enumerate(unanswered, start=1)
            }
            update_job_draft(
                update.effective_user.id,
                job_id,
                {"pending_questions": pending, "ready": False},
            )
            await update.message.reply_text(
                "This job has extra questions:\n"
                f"{_format_questions(unanswered)}\n\n"
                "Answer with: /answer <job_id> <q_id> <answer>\n"
                "List pending with: /questions <job_id>"
            )
            return States.GENERIC_MESSAGE

        update_job_draft(
            update.effective_user.id,
            job_id,
            {"pending_questions": {}, "ready": True},
        )
        await update.message.reply_text(
            f"Application is ready. Confirm submission with /confirm {job_id}."
        )
    except Exception as e:
        logger.error(f"Error applying to job {job_id}: {e}")
        await update.message.reply_text(
            f"Failed to apply to job {job_id}. Check logs for details."
        )

    return States.GENERIC_MESSAGE


async def skip_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Discard a prepared job draft."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /skip <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    delete_job_draft(update.effective_user.id, job_id)
    await update.message.reply_text(f"Discarded draft for job {job_id}.")
    return States.GENERIC_MESSAGE


async def approve_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Mark a draft as approved."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /approve <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    update_job_draft(update.effective_user.id, job_id, {"approved": True})
    await update.message.reply_text(
        f"Approved draft for job {job_id}. Use /apply {job_id} to submit."
    )
    return States.GENERIC_MESSAGE


async def confirm_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Submit a prepared application after explicit confirmation."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /confirm <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    if not job.get("approved"):
        await update.message.reply_text(
            "This draft isn't approved yet. Use /approve <job_id>."
        )
        return States.GENERIC_MESSAGE

    if not job.get("ready"):
        await update.message.reply_text(
            "This application isn't ready. Run /apply <job_id> first."
        )
        return States.GENERIC_MESSAGE

    await update.message.reply_text(f"Submitting application for job {job_id}...")

    try:
        from telegram_bot.parsers.djinni import DjinniParser

        parser = DjinniParser(
            email=settings.get("DJINNI_EMAIL"),
            password=settings.get("DJINNI_PASSWORD"),
        )
        answers = get_all_answers()
        unanswered = await parser.apply_to_job(
            job_id=job_id,
            message=job["cover_letter"],
            answers=answers,
            page_num=int(job.get("page_num", 1)),
        )
        if unanswered:
            pending = {
                f"q{idx}": question.model_dump()
                for idx, question in enumerate(unanswered, start=1)
            }
            update_job_draft(
                update.effective_user.id,
                job_id,
                {"pending_questions": pending, "ready": False},
            )
            await update.message.reply_text(
                "This job has extra questions:\n"
                f"{_format_questions(unanswered)}\n\n"
                "Answer with: /answer <job_id> <q_id> <answer>\n"
                "Then run /apply <job_id> again."
            )
            return States.GENERIC_MESSAGE

        delete_job_draft(update.effective_user.id, job_id)
        await update.message.reply_text(f"Applied to job {job_id}.")
    except Exception as e:
        logger.error(f"Error applying to job {job_id}: {e}")
        await update.message.reply_text(
            f"Failed to apply to job {job_id}. Check logs for details."
        )

    return States.GENERIC_MESSAGE


async def list_questions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """List pending questions for a job."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /questions <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    pending = job.get("pending_questions", {})
    if not pending:
        await update.message.reply_text("No pending questions for this job.")
        return States.GENERIC_MESSAGE

    lines = []
    for key, question in pending.items():
        text = str(question.get("text", "")).strip()
        options = question.get("options")
        options_text = ""
        if isinstance(options, list) and options:
            options_text = " Options: " + ", ".join([str(o) for o in options])
        lines.append(f"{key}: {text}{options_text}")

    await update.message.reply_text("\n".join(lines))
    return States.GENERIC_MESSAGE


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Store a global answer and clear the pending question."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if len(context.args) < 3:
        await update.message.reply_text("Usage: /answer <job_id> <q_id> <answer>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    q_id = context.args[1]
    answer = " ".join(context.args[2:]).strip()

    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    pending = job.get("pending_questions", {})
    question = pending.get(q_id)
    if not question:
        await update.message.reply_text(
            "Unknown question id. Use /questions <job_id> to list pending."
        )
        return States.GENERIC_MESSAGE

    q_text = str(question.get("text", "")).strip()
    if not q_text:
        await update.message.reply_text("Question text missing. Try /apply again.")
        return States.GENERIC_MESSAGE

    set_answer(q_text, answer)
    pending.pop(q_id, None)
    update_job_draft(
        update.effective_user.id,
        job_id,
        {"pending_questions": pending, "ready": False},
    )
    await update.message.reply_text(
        f"Saved answer globally. Run /apply {job_id} to re-check."
    )
    return States.GENERIC_MESSAGE


async def edit_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Start editing a draft cover letter."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /edit <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    context.user_data["edit_job_id"] = job_id
    await update.message.reply_text(
        "Send the updated cover letter text. I'll replace the draft."
    )
    return States.EDIT_COVER_LETTER


async def edit_job_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Save edited cover letter text."""
    if not (update.effective_user and update.message and update.message.text):
        return States.GENERIC_MESSAGE

    job_id = context.user_data.get("edit_job_id")
    if not job_id:
        return States.GENERIC_MESSAGE

    update_job_draft(
        update.effective_user.id,
        job_id,
        {"cover_letter": update.message.text, "approved": False},
    )
    context.user_data.pop("edit_job_id", None)
    await update.message.reply_text(
        f"Updated draft for job {job_id}. Use /approve {job_id} to confirm."
    )
    return States.GENERIC_MESSAGE


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

    request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=60,
        write_timeout=20,
        pool_timeout=20,
    )
    app = Application.builder().token(token).request(request).build()

    logger.info("Starting bot... okay, registering handlers")

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("set_cv", set_cv_start),
            CommandHandler("cover_letter", cover_letter_start),
            CommandHandler("djinni_jobs", djinni_jobs),
            CommandHandler("apply", apply_job),
            CommandHandler("skip", skip_job),
            CommandHandler("approve", approve_job),
            CommandHandler("edit", edit_job_start),
            CommandHandler("confirm", confirm_job),
            CommandHandler("questions", list_questions),
            CommandHandler("answer", answer_question),
        ],  # ty:ignore[invalid-argument-type]
        states={
            States.GENERIC_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reply)
            ],
            States.SET_CV: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_cv_save)
            ],
            States.COVER_LETTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cover_letter_generate)
            ],
            States.EDIT_COVER_LETTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_job_save)
            ],
        },  # ty:ignore[invalid-argument-type]
        fallbacks=[CommandHandler("cancel", cancel)],  # ty:ignore[invalid-argument-type]
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    # Handle the case when a user sends /start but they're not in a conversation
    app.add_handler(CommandHandler("start", start))

    app.run_polling()


if __name__ == "__main__":
    main()
