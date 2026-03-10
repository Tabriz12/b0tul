import os
from enum import IntEnum

from telegram import BotCommand, ReplyKeyboardRemove, Update
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
from telegram_bot.helpers.user_profile_data import (
    add_answer_example,
    get_answer_examples,
    get_user_preferences,
    set_user_preferences,
)
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
    EDIT_COVER_LETTER_LLM = 5
    EDIT_QUESTION_ANSWER_LLM = 6


SUPPORTED_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("start", "Start the bot"),
    BotCommand("set_cv", "Save your CV text"),
    BotCommand("cover_letter", "Generate a cover letter from a job description"),
    BotCommand("djinni_jobs", "Fetch jobs (page/limit) and draft cover letters"),
    BotCommand("cancel", "Cancel current input flow"),
    BotCommand("approve", "Approve a draft"),
    BotCommand("edit", "Edit a draft cover letter"),
    BotCommand("edit_ai", "Edit a draft cover letter with AI"),
    BotCommand("apply", "Prepare application and check extra questions"),
    BotCommand("confirm", "Submit the application"),
    BotCommand("skip", "Discard a draft"),
    BotCommand("questions", "List pending questions for a job"),
    BotCommand("answer", "Answer a pending question"),
    BotCommand("edit_answer_ai", "Revise generated answer for a pending question"),
    BotCommand("save_answer", "Save generated answer for a pending question"),
    BotCommand("set_prefs", "Set your answering preferences/style"),
    BotCommand("prefs", "Show your saved preferences"),
)
SUPPORTED_COMMAND_NAMES: tuple[str, ...] = tuple(
    command.command for command in SUPPORTED_COMMANDS
)


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


def _extract_page_limit_keywords(args: list[str]) -> tuple[int, int | None, list[str]]:
    page_num = 1
    limit: int | None = None
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


def _get_pending_question(
    user_id: int, job_id: str, q_id: str
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    job = get_job_draft(user_id, job_id)
    if not job:
        return None, None
    pending = job.get("pending_questions", {})
    if not isinstance(pending, dict):
        return job, None
    question = pending.get(q_id)
    if not isinstance(question, dict):
        return job, None
    return job, question


def _finalize_pending_answer(user_id: int, job_id: str, q_id: str, answer: str) -> bool:
    job = get_job_draft(user_id, job_id)
    if not job:
        return False

    pending = job.get("pending_questions", {})
    if not isinstance(pending, dict):
        return False

    question = pending.get(q_id)
    if not isinstance(question, dict):
        return False

    q_text = str(question.get("text", "")).strip()
    if not q_text:
        return False

    clean_answer = answer.strip()
    if not clean_answer:
        return False

    set_answer(q_text, clean_answer)
    add_answer_example(user_id, q_text, clean_answer)
    pending.pop(q_id, None)
    update_job_draft(user_id, job_id, {"pending_questions": pending, "ready": False})
    return True


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
            f"Edit with AI: /edit_ai {job_id}\n"
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
                "Draft answer: /answer <job_id> <q_id>\n"
                "Edit draft: /edit_answer_ai <job_id> <q_id>\n"
                "Save draft: /save_answer <job_id> <q_id>\n"
                "Manual answer: /answer <job_id> <q_id> <answer>\n"
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
                "Draft answer: /answer <job_id> <q_id>\n"
                "Edit draft: /edit_answer_ai <job_id> <q_id>\n"
                "Save draft: /save_answer <job_id> <q_id>\n"
                "Manual answer: /answer <job_id> <q_id> <answer>\n"
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
        draft_answer = str(question.get("draft_answer", "")).strip()
        options_text = ""
        if isinstance(options, list) and options:
            options_text = " Options: " + ", ".join([str(o) for o in options])
        draft_text = ""
        if draft_answer:
            draft_text = f"\nDraft: {_preview_text(draft_answer, limit=200)}"
        lines.append(f"{key}: {text}{options_text}{draft_text}")

    await update.message.reply_text(
        "\n\n".join(lines)
        + "\n\nDraft: /answer <job_id> <q_id>\n"
        + "Edit draft: /edit_answer_ai <job_id> <q_id>\n"
        + "Save draft: /save_answer <job_id> <q_id>\n"
        + "Manual answer: /answer <job_id> <q_id> <answer>"
    )
    return States.GENERIC_MESSAGE


async def answer_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Draft an answer with AI or save a manual answer."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/answer <job_id> <q_id>  # generate AI draft\n"
            "/answer <job_id> <q_id> <answer>  # save manual answer"
        )
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    q_id = context.args[1]
    manual_answer = " ".join(context.args[2:]).strip() if len(context.args) > 2 else ""

    user_id = update.effective_user.id
    job, question = _get_pending_question(user_id, job_id, q_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    if not question:
        await update.message.reply_text(
            "Unknown question id. Use /questions <job_id> to list pending."
        )
        return States.GENERIC_MESSAGE

    q_text = str(question.get("text", "")).strip()
    if not q_text:
        await update.message.reply_text("Question text missing. Try /apply again.")
        return States.GENERIC_MESSAGE

    if manual_answer:
        saved = _finalize_pending_answer(user_id, job_id, q_id, manual_answer)
        if not saved:
            await update.message.reply_text("Failed to save answer. Try /apply again.")
            return States.GENERIC_MESSAGE

        await update.message.reply_text(
            f"Saved answer globally. Run /apply {job_id} to re-check."
        )
        return States.GENERIC_MESSAGE

    cv = get_user_cv(user_id) or ""
    job_description = str(job.get("description", "")).strip()
    question_type = str(question.get("type", "text")).strip()
    raw_options = question.get("options", [])
    options = [str(opt) for opt in raw_options] if isinstance(raw_options, list) else []
    preferences = get_user_preferences(user_id)
    examples = get_answer_examples(user_id, limit=8)

    try:
        draft_answer = ollama_client.generate_question_answer_template(
            cv=cv,
            job_description=job_description,
            question_text=q_text,
            question_type=question_type,
            options=options,
            user_preferences=preferences,
            answer_examples=examples,
        )
    except Exception as e:
        logger.error(f"Error generating answer draft for job {job_id} {q_id}: {e}")
        await update.message.reply_text("Failed to generate answer draft.")
        return States.GENERIC_MESSAGE

    pending = job.get("pending_questions", {})
    if not isinstance(pending, dict):
        await update.message.reply_text(
            "Pending questions are unavailable. Try /apply."
        )
        return States.GENERIC_MESSAGE

    question["draft_answer"] = draft_answer
    pending[q_id] = question
    update_job_draft(user_id, job_id, {"pending_questions": pending, "ready": False})

    await update.message.reply_text(
        f"Draft answer for {q_id}:\n{draft_answer}\n\n"
        f"Edit with AI: /edit_answer_ai {job_id} {q_id}\n"
        f"Save draft: /save_answer {job_id} {q_id}\n"
        f"Or save manual: /answer {job_id} {q_id} <answer>"
    )
    return States.GENERIC_MESSAGE


async def save_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Save the generated draft answer for a pending question."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /save_answer <job_id> <q_id>")
        return States.GENERIC_MESSAGE

    user_id = update.effective_user.id
    job_id = context.args[0]
    q_id = context.args[1]
    job, question = _get_pending_question(user_id, job_id, q_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE
    if not question:
        await update.message.reply_text(
            "Unknown question id. Use /questions <job_id> to list pending."
        )
        return States.GENERIC_MESSAGE

    draft_answer = str(question.get("draft_answer", "")).strip()
    if not draft_answer:
        await update.message.reply_text(
            "No generated draft found. Run /answer <job_id> <q_id> first."
        )
        return States.GENERIC_MESSAGE

    saved = _finalize_pending_answer(user_id, job_id, q_id, draft_answer)
    if not saved:
        await update.message.reply_text("Failed to save answer. Try /apply again.")
        return States.GENERIC_MESSAGE

    await update.message.reply_text(
        f"Saved draft answer globally. Run /apply {job_id} to re-check."
    )
    return States.GENERIC_MESSAGE


async def edit_answer_ai_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Start AI-based revision of a generated answer draft."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /edit_answer_ai <job_id> <q_id>")
        return States.GENERIC_MESSAGE

    user_id = update.effective_user.id
    job_id = context.args[0]
    q_id = context.args[1]
    _, question = _get_pending_question(user_id, job_id, q_id)
    if not question:
        await update.message.reply_text(
            "Unknown question id. Use /questions <job_id> to list pending."
        )
        return States.GENERIC_MESSAGE

    draft_answer = str(question.get("draft_answer", "")).strip()
    if not draft_answer:
        await update.message.reply_text(
            "No generated draft found. Run /answer <job_id> <q_id> first."
        )
        return States.GENERIC_MESSAGE

    context.user_data["edit_answer_job_id"] = job_id
    context.user_data["edit_answer_q_id"] = q_id
    await update.message.reply_text(
        f"Current draft for {q_id}:\n{draft_answer}\n\nSend what you want to change."
    )
    return States.EDIT_QUESTION_ANSWER_LLM


async def edit_answer_ai_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Apply AI-requested changes to generated answer draft."""
    if not (update.effective_user and update.message and update.message.text):
        return States.GENERIC_MESSAGE

    user_id = update.effective_user.id
    job_id = context.user_data.get("edit_answer_job_id")
    q_id = context.user_data.get("edit_answer_q_id")
    if not (job_id and q_id):
        return States.GENERIC_MESSAGE

    job, question = _get_pending_question(user_id, str(job_id), str(q_id))
    if not job or not question:
        context.user_data.pop("edit_answer_job_id", None)
        context.user_data.pop("edit_answer_q_id", None)
        await update.message.reply_text("Question is no longer pending.")
        return States.GENERIC_MESSAGE

    q_text = str(question.get("text", "")).strip()
    draft_answer = str(question.get("draft_answer", "")).strip()
    if not draft_answer or not q_text:
        context.user_data.pop("edit_answer_job_id", None)
        context.user_data.pop("edit_answer_q_id", None)
        await update.message.reply_text(
            "No draft answer to edit. Run /answer <job_id> <q_id> first."
        )
        return States.GENERIC_MESSAGE

    try:
        revised = ollama_client.revise_question_answer(
            answer_draft=draft_answer,
            question_text=q_text,
            change_request=update.message.text,
        )
    except Exception as e:
        logger.error(f"Error revising answer draft for job {job_id} {q_id}: {e}")
        context.user_data.pop("edit_answer_job_id", None)
        context.user_data.pop("edit_answer_q_id", None)
        await update.message.reply_text("Failed to revise answer draft.")
        return States.GENERIC_MESSAGE

    pending = job.get("pending_questions", {})
    if isinstance(pending, dict):
        question["draft_answer"] = revised
        pending[str(q_id)] = question
        update_job_draft(
            user_id,
            str(job_id),
            {"pending_questions": pending, "ready": False},
        )

    context.user_data.pop("edit_answer_job_id", None)
    context.user_data.pop("edit_answer_q_id", None)

    await update.message.reply_text(
        f"Updated draft for {q_id}:\n{revised}\n\n"
        f"Save it: /save_answer {job_id} {q_id}\n"
        f"Or edit again: /edit_answer_ai {job_id} {q_id}"
    )
    return States.GENERIC_MESSAGE


async def set_preferences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> States:
    """Save user preferences used for drafting answers."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /set_prefs <your preferences>")
        return States.GENERIC_MESSAGE

    preferences = " ".join(context.args).strip()
    if not preferences:
        await update.message.reply_text("Preferences cannot be empty.")
        return States.GENERIC_MESSAGE

    set_user_preferences(update.effective_user.id, preferences)
    await update.message.reply_text("Saved preferences for future answer drafts.")
    return States.GENERIC_MESSAGE


async def show_preferences(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Show saved user preferences."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    preferences = get_user_preferences(update.effective_user.id)
    if not preferences:
        await update.message.reply_text(
            "No preferences saved yet. Set them with /set_prefs <text>."
        )
        return States.GENERIC_MESSAGE

    await update.message.reply_text(f"Current preferences:\n{preferences}")
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


async def edit_job_ai_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Start AI-based editing of a draft cover letter."""
    if not (update.effective_user and update.message):
        return States.GENERIC_MESSAGE

    if not context.args:
        await update.message.reply_text("Usage: /edit_ai <job_id>")
        return States.GENERIC_MESSAGE

    job_id = context.args[0]
    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    context.user_data["edit_ai_job_id"] = job_id
    await update.message.reply_text(
        "Send the changes you want (tone, style, points to add/remove)."
    )
    return States.EDIT_COVER_LETTER_LLM


async def edit_job_ai_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> States:
    """Apply user-requested edits to a cover letter using the LLM."""
    if not (update.effective_user and update.message and update.message.text):
        return States.GENERIC_MESSAGE

    job_id = context.user_data.get("edit_ai_job_id")
    if not job_id:
        return States.GENERIC_MESSAGE

    job = get_job_draft(update.effective_user.id, job_id)
    if not job:
        context.user_data.pop("edit_ai_job_id", None)
        await update.message.reply_text(
            "No draft found for that job. Run /djinni_jobs first."
        )
        return States.GENERIC_MESSAGE

    current_cover_letter = str(job.get("cover_letter", "")).strip()
    if not current_cover_letter:
        context.user_data.pop("edit_ai_job_id", None)
        await update.message.reply_text(
            "Current draft is empty. Generate a draft first with /djinni_jobs."
        )
        return States.GENERIC_MESSAGE

    try:
        revised = ollama_client.revise_cover_letter(
            current_cover_letter, update.message.text
        )
    except Exception as e:
        logger.error(f"Error revising cover letter for job {job_id}: {e}")
        await update.message.reply_text(
            "Failed to revise the draft with AI. Please try again."
        )
        return States.GENERIC_MESSAGE

    update_job_draft(
        update.effective_user.id,
        job_id,
        {"cover_letter": revised, "approved": False, "ready": False},
    )
    context.user_data.pop("edit_ai_job_id", None)
    await update.message.reply_text(
        f"AI updated draft for job {job_id}:\n\n{revised}\n\n"
        f"Use /approve {job_id} to confirm."
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


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not (update.message and update.message.text):
        return
    command = update.message.text.split()[0]
    available = ", ".join([f"/{name}" for name in SUPPORTED_COMMAND_NAMES])
    await update.message.reply_text(
        f"Unknown command: {command}\nAvailable commands: {available}"
    )


def get_token() -> str | None:
    token = settings.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    return os.environ.get("TELEGRAM_BOT_TOKEN")


async def _set_bot_commands(application: Application) -> None:
    """Register command list shown in Telegram UI."""
    await application.bot.set_my_commands(list(SUPPORTED_COMMANDS))


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
    app = (
        Application.builder()
        .token(token)
        .request(request)
        .post_init(_set_bot_commands)
        .build()
    )

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
            CommandHandler("edit_ai", edit_job_ai_start),
            CommandHandler("confirm", confirm_job),
            CommandHandler("questions", list_questions),
            CommandHandler("answer", answer_question),
            CommandHandler("save_answer", save_answer),
            CommandHandler("edit_answer_ai", edit_answer_ai_start),
            CommandHandler("set_prefs", set_preferences),
            CommandHandler("prefs", show_preferences),
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
            States.EDIT_COVER_LETTER_LLM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_job_ai_save)
            ],
            States.EDIT_QUESTION_ANSWER_LLM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_answer_ai_save)
            ],
        },  # ty:ignore[invalid-argument-type]
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.COMMAND, unknown_command),
        ],  # ty:ignore[invalid-argument-type]
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    # Handle the case when a user sends /start but they're not in a conversation
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.run_polling()


if __name__ == "__main__":
    main()
