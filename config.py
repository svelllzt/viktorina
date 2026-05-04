import json
from pathlib import Path

_RUNTIME = Path(__file__).resolve().with_name("runtime_config.json")

_DEFAULTS = {
    "ADMIN_EMAIL": "admin@gmail.com",
    "ADMIN_PASSWORD": "11223344",
    "SMTP_HOST": "smtp.gmail.com",
    "SMTP_PORT": 587,
    "SMTP_USER": "admin@gmail.com",
    "SMTP_PASSWORD": "your_app_password",
    "SMTP_FROM": "noreply@quizapp.com",
}


def get_settings() -> dict:
    data = dict(_DEFAULTS)
    if _RUNTIME.exists():
        try:
            data.update(json.loads(_RUNTIME.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    data["SMTP_PORT"] = int(data.get("SMTP_PORT") or 587)
    return data


def save_settings(updates: dict) -> dict:
    cur = get_settings()
    for k, v in updates.items():
        if k in _DEFAULTS:
            cur[k] = v
    _RUNTIME.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    return cur
