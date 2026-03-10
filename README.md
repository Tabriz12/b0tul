# Telegram Job Assistant Bot

A Telegram bot that parses job listings (starting with Djinni), uses local LLMs (Ollama), and generates personalized cover letters based on a user's CV. The design is modular so new job platforms and LLM backends can be added.

## Quick Start
1. Set configuration in `settings.toml` or `.secrets.toml`.
2. Install dependencies: `poetry install`.
3. Run the bot: `poetry run python -m telegram_bot.main`.

## Docker Stack
- Build and run services: `docker compose up --build`.
- Pull a local model: `docker compose exec ollama ollama pull gemma3`.
- Install playwright dependencies: `docker compose exec telegram_bot poetry run python -m playwright install chromium`.

## Configuration
- Required:
  - `DYNACONF_TELEGRAM_BOT_TOKEN`
  - `DYNACONF_OLLAMA_MODEL` (e.g., `qwen3:8b`)
- Djinni integration:
  - `DYNACONF_DJINNI_EMAIL`
  - `DYNACONF_DJINNI_PASSWORD`
- Optional:
  - `DYNACONF_OLLAMA_WEBSEARCH_API_KEY`

## Bot Commands
- `/start` — start the bot.
- `/set_cv` — store your CV text for cover letter generation.
- `/cover_letter` — paste a job description and receive a tailored cover letter.
- `/djinni_jobs [page] [limit] [keywords...]` — fetch Djinni jobs, draft cover letters, and wait for approval. Keywords can be comma-separated.
- `/approve <job_id>` — approve a draft before applying.
- `/edit <job_id>` — replace the draft with your own text.
- `/apply <job_id>` — prepare the application and check for extra questions (no submit).
- `/confirm <job_id>` — submit the approved, prepared application.
- `/skip <job_id>` — discard a prepared draft.
- `/questions <job_id>` — list any extra questions found.
- `/answer <job_id> <q_id> <answer>` — store a global answer for a question.

## Project Goal
The bot collects a user's CV, parses job listings, and uses LLMs to draft personalized cover letters. It is designed to be extended to other job platforms and LLM providers.
