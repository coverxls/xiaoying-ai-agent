from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StorageError(RuntimeError):
    """Raised when the draft store cannot persist a draft."""


class JsonDraftStore:
    """A tiny JSON-file draft store with an explicit idempotency key.

    The store only knows about drafts. It has no publish method, so an
    over-eager model cannot turn a draft into a real publishing action.
    """

    def __init__(self, path: str | Path, fail_save: bool = False):
        self.path = Path(path)
        self.fail_save = fail_save
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "drafts": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StorageError(f"draft store is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("drafts"), list):
            raise StorageError("draft store must contain a drafts list")
        return raw

    def _flush(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @staticmethod
    def make_key(
        material_id: str,
        date: str,
        slot: str,
        requested_time: str,
        draft_status: str,
    ) -> str:
        raw = "|".join(
            [material_id.strip(), date.strip(), slot.strip(), requested_time.strip(), draft_status.strip()]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def save(self, draft: dict[str, Any]) -> dict[str, Any]:
        if self.fail_save:
            raise StorageError("草稿存储不可用（注入故障）")

        key = self.make_key(
            draft["material_id"],
            draft["date"],
            draft["slot"],
            draft["requested_time"],
            draft["draft_status"],
        )
        for existing in self._data["drafts"]:
            if existing.get("idempotency_key") == key:
                return {
                    "draft_id": existing["draft_id"],
                    "duplicate": True,
                    "draft": existing,
                }

        draft_id = "D-" + key[:10]
        record = {
            "draft_id": draft_id,
            "idempotency_key": key,
            **draft,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "published": False,
        }
        self._data["drafts"].append(record)
        self._flush()
        return {"draft_id": draft_id, "duplicate": False, "draft": record}

    def list_drafts(self) -> list[dict[str, Any]]:
        return list(self._data["drafts"])

    def reset(self) -> None:
        self._data = {"version": 1, "drafts": []}
        self._flush()
