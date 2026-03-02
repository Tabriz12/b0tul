import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")
USER_DATA_FILE = DATA_DIR / "user_profiles.json"


def _load_data() -> dict[str, Any]:
    if not USER_DATA_FILE.exists():
        return {}
    try:
        return json.loads(USER_DATA_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_data(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_DATA_FILE.write_text(json.dumps(data, indent=2))


def get_user_cv(user_id: int) -> str | None:
    data = _load_data()
    profile = data.get(str(user_id), {})
    cv = profile.get("cv")
    if isinstance(cv, str) and cv.strip():
        return cv.strip()
    return None


def set_user_cv(user_id: int, cv_text: str) -> None:
    data = _load_data()
    profile = data.get(str(user_id), {})
    profile["cv"] = cv_text.strip()
    data[str(user_id)] = profile
    _save_data(data)
