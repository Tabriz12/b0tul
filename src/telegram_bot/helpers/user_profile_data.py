import json
from pathlib import Path
from typing import Any

from telegram_bot.helpers.answers_data import normalize_question

DATA_DIR = Path("data")
PROFILES_FILE = DATA_DIR / "user_profiles.json"
MAX_EXAMPLES = 50


def _load_data() -> dict[str, Any]:
    if not PROFILES_FILE.exists():
        return {}
    try:
        return json.loads(PROFILES_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_data(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps(data, indent=2))


def _get_or_create_user_profile(data: dict[str, Any], user_id: int) -> dict[str, Any]:
    key = str(user_id)
    profile = data.get(key)
    if not isinstance(profile, dict):
        profile = {}
    data[key] = profile
    return profile


def get_user_preferences(user_id: int) -> str:
    data = _load_data()
    profile = data.get(str(user_id), {})
    if not isinstance(profile, dict):
        return ""
    preferences = profile.get("preferences")
    if isinstance(preferences, str):
        return preferences.strip()
    return ""


def set_user_preferences(user_id: int, preferences: str) -> None:
    data = _load_data()
    profile = _get_or_create_user_profile(data, user_id)
    profile["preferences"] = preferences.strip()
    _save_data(data)


def add_answer_example(user_id: int, question: str, answer: str) -> None:
    normalized_question = normalize_question(question)
    clean_answer = answer.strip()
    if not normalized_question or not clean_answer:
        return

    data = _load_data()
    profile = _get_or_create_user_profile(data, user_id)
    raw_examples = profile.get("answer_examples", [])
    examples: list[dict[str, str]] = []
    if isinstance(raw_examples, list):
        for item in raw_examples:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question", "")).strip()
            a = str(item.get("answer", "")).strip()
            if q and a:
                examples.append({"question": q, "answer": a})

    examples = [item for item in examples if item["question"] != normalized_question]
    examples.insert(0, {"question": normalized_question, "answer": clean_answer})
    profile["answer_examples"] = examples[:MAX_EXAMPLES]
    _save_data(data)


def get_answer_examples(user_id: int, limit: int = 8) -> list[dict[str, str]]:
    data = _load_data()
    profile = data.get(str(user_id), {})
    if not isinstance(profile, dict):
        return []

    raw_examples = profile.get("answer_examples", [])
    if not isinstance(raw_examples, list):
        return []

    examples: list[dict[str, str]] = []
    for item in raw_examples:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            examples.append({"question": question, "answer": answer})
        if len(examples) >= max(limit, 0):
            break

    return examples
