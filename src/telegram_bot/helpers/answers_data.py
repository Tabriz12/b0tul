import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")
ANSWERS_FILE = DATA_DIR / "global_answers.json"


def _load_data() -> dict[str, Any]:
    if not ANSWERS_FILE.exists():
        return {}
    try:
        return json.loads(ANSWERS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_data(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ANSWERS_FILE.write_text(json.dumps(data, indent=2))


def normalize_question(text: str) -> str:
    return " ".join(text.lower().split())


def get_all_answers() -> dict[str, str]:
    data = _load_data()
    answers = data.get("answers", {})
    if isinstance(answers, dict):
        return {str(k): str(v) for k, v in answers.items()}
    return {}


def set_answer(question: str, answer: str) -> None:
    data = _load_data()
    answers = data.get("answers", {})
    if not isinstance(answers, dict):
        answers = {}
    answers[normalize_question(question)] = answer.strip()
    data["answers"] = answers
    _save_data(data)
