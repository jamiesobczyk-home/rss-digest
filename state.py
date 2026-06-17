import json
import os
from datetime import datetime, timedelta, timezone


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"last_run": None, "seen": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def prune(state: dict, days: int = 30) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    state["seen"] = {
        k: v for k, v in state["seen"].items() if v >= cutoff
    }
    return state


def mark_seen(state: dict, article_ids: list[str], date_str: str) -> dict:
    for aid in article_ids:
        state["seen"][aid] = date_str
    return state


def is_seen(state: dict, article_id: str) -> bool:
    return article_id in state["seen"]
