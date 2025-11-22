from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

DEFAULT_PATH = Path(
    os.environ.get(
        "USERS_FILE",
        Path(__file__).parent / "data" / "users_registry.json",
    )
)


def _load(path: Path = DEFAULT_PATH) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save(data: Dict[str, dict], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_user(telegram_id: int, name: str | None, username: str | None) -> Tuple[dict, bool, int]:
    data = _load()
    key = str(telegram_id)
    is_new = key not in data
    now = datetime.utcnow().isoformat()
    if is_new:
        entry = {
            "telegram_id": telegram_id,
            "created_at": now,
        }
    else:
        entry = data[key]
    entry["name"] = name
    entry["username"] = username
    entry["updated_at"] = now
    data[key] = entry
    _save(data)
    return entry, is_new, len(data)


def get_total_users_file() -> int:
    return len(_load())


def get_known_user_ids() -> set[int]:
    return {int(key) for key in _load().keys()}
