import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")
JOBS_FILE = DATA_DIR / "user_jobs.json"


def _load_data() -> dict[str, Any]:
    if not JOBS_FILE.exists():
        return {}
    try:
        return json.loads(JOBS_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_data(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(data, indent=2))


def save_job_draft(user_id: int, job_id: str, payload: dict[str, Any]) -> None:
    data = _load_data()
    user_jobs = data.get(str(user_id), {})
    user_jobs[job_id] = payload
    data[str(user_id)] = user_jobs
    _save_data(data)


def get_job_draft(user_id: int, job_id: str) -> dict[str, Any] | None:
    data = _load_data()
    user_jobs = data.get(str(user_id), {})
    job = user_jobs.get(job_id)
    if isinstance(job, dict):
        return job
    return None


def update_job_draft(user_id: int, job_id: str, updates: dict[str, Any]) -> None:
    data = _load_data()
    user_jobs = data.get(str(user_id), {})
    job = user_jobs.get(job_id)
    if not isinstance(job, dict):
        return
    job.update(updates)
    user_jobs[job_id] = job
    data[str(user_id)] = user_jobs
    _save_data(data)


def delete_job_draft(user_id: int, job_id: str) -> None:
    data = _load_data()
    user_jobs = data.get(str(user_id), {})
    if job_id in user_jobs:
        del user_jobs[job_id]
        data[str(user_id)] = user_jobs
        _save_data(data)
