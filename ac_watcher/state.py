"""
Tiny JSON-file state store.

We remember, per target (and per listing id for Kleinanzeigen), whether it
was in stock / within budget the last time we checked. This lets us notify
only on a *transition* into "buyable", instead of spamming you every 4
minutes while something stays in stock.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

_LOCK = threading.Lock()


class StateStore:
    def __init__(self, path: str = "state.json"):
        self.path = path
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                try:
                    self._data = json.load(f)
                except json.JSONDecodeError:
                    self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.path)

    def get(self, key: str, default=None):
        with _LOCK:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with _LOCK:
            self._data[key] = value
            self._save()
