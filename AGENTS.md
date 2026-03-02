# Repository Guidelines

## Project Structure & Module Organization
- `src/telegram_bot/` is the main package. Entry point is `src/telegram_bot/main.py` and submodules live in `helpers/`, `llm/`, `parsers/`, and `logs/`.
- `tests/` exists but is currently empty; add new tests here and mirror the package layout.
- `compose.yaml` defines the local stack (bot + Ollama + Open WebUI).
- `config.py`, `settings.toml`, and optional `.secrets.toml` hold runtime configuration via Dynaconf.
- `main.Dockerfile` provides a containerized build using Poetry.

## Build, Test, and Development Commands
- `poetry install` installs dependencies locally.
- `poetry run python -m telegram_bot.main` runs the bot module directly.
- `docker compose up --build` builds and starts the full stack.
- `docker compose exec ollama ollama pull gemma3` pulls the local LLM model (from `README.md`).
- `docker build -t b0tulbot:0.1 .` builds the image (see `main.Dockerfile`).

## Coding Style & Naming Conventions
- Python 3.12, 4-space indentation, line length 88 (see `pyproject.toml`).
- Formatting and linting use Ruff. Recommended commands:
  - `ruff format src tests`
  - `ruff check --fix src tests`
- Use double quotes for strings (Ruff formatter default).
- Module names are lowercase; keep new files consistent with existing package layout.

## Testing Guidelines
- No test runner is configured yet and `tests/` is empty. If you add tests, document the runner you choose and keep tests named `test_*.py`.
- When adding tests, include clear coverage notes in the PR description.

## Commit & Pull Request Guidelines
- Commit history uses short, descriptive, imperative phrases (e.g., “ollama integration”). Keep messages concise and scoped.
- PRs should include:
  - Summary of changes.
  - How to run or verify (commands + expected behavior).
  - Any config or settings updates (e.g., `settings.toml`, `.secrets.toml`).

## Configuration & Secrets
- Runtime settings load from `settings.toml` and `.secrets.toml` via Dynaconf.
- Environment overrides use the `DYNACONF_` prefix (e.g., `DYNACONF_OLLAMA_MODEL=qwen3:8b`).


## Project info
- This project is a Telegram bot that integrates with local LLMs (like Ollama) and Open WebUI to provide AI-powered interactions. The bot is designed to be modular and extensible
- The goal is to parse djinni website job listings and apply LLMs to generate personalized cover letters for users and apply. The bot can also be extended to support other job platforms and LLMs in the future. 
- Before applying user needs to provide their CV if not already provided and the bot will use it to generate personalized cover letters.
- Creating cover letters will be done by parsing the job listing and extracting relevant information, then using that information along with the user's CV to generate a tailored cover letter using the LLM.