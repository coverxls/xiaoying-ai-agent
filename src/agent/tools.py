from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .models import ToolResult, utc_now
from .store import JsonDraftStore, StorageError


REQUIRED_FIELDS = ["title", "content_type", "duration_sec", "platform", "rights_status"]
PLATFORM_LABELS = {"douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站"}


def _parse_hhmm(value: str) -> int:
    try:
        t = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"时间必须是 HH:MM，收到 {value!r}") from exc
    return t.hour * 60 + t.minute


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"日期必须是 YYYY-MM-DD，收到 {value!r}") from exc
    return value


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _resolution_rank(value: str) -> int:
    ranks = {"480p": 1, "720p": 2, "1080p": 3, "2k": 4, "4k": 5}
    return ranks.get(str(value).lower(), 0)


TOOL_SPECS = """\
- get_material(material_id: str)
  读取素材元数据。返回 material 和 missing_fields；missing_fields 非空时不要猜。
- check_rules(material_id: str)
  按 data/rules.json 检查版权、敏感词、平台时长、分辨率。返回 blocked、risks、warnings。
- query_slots(date: str, platform: str, duration_sec: int, preferred_time: str)
  查询某日某平台的占用情况。返回 requested.available、conflicts、alternatives、nearest_alternative。
- save_draft(material_id, date, slot, requested_time, draft_status, risk_summary, pending_items, note="")
  只保存草稿，不发布。draft_status 只能是 ready 或 pending_confirmation。
  同一 material/date/slot/requested_time/draft_status 重复保存会返回 duplicate=true。
- finish(status, message, pending_items=[])
  status 只能是 completed、pending_confirmation、needs_input、blocked、save_failed。
"""


class ToolRegistry:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        store: JsonDraftStore | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).resolve().parents[2] / "data"
        self.materials = self._read_json("materials.json")
        self.rules = self._read_json("rules.json")["rules"]
        self.schedule = self._read_json("schedule.json")
        self.store = store or JsonDraftStore(Path("runtime/drafts.json"))
        # Drafts created in the current batch reserve their slot so that a
        # later material cannot be offered the same window.
        self.reservations: list[dict[str, Any]] = []
        self._tools: dict[str, Callable[..., ToolResult]] = {
            "get_material": self.get_material,
            "check_rules": self.check_rules,
            "query_slots": self.query_slots,
            "save_draft": self.save_draft,
        }

    def _read_json(self, name: str) -> dict[str, Any]:
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        fn = self._tools.get(name)
        if fn is None:
            return ToolResult(
                ok=False,
                error="unknown_tool",
                message=f"未知工具 {name!r}；可用工具：{', '.join(self._tools)}",
            )
        if not isinstance(args, dict):
            return ToolResult(ok=False, error="invalid_arguments", message="args 必须是 JSON 对象")
        try:
            return fn(**args)
        except TypeError as exc:
            return ToolResult(ok=False, error="invalid_arguments", message=str(exc))
        except ValueError as exc:
            return ToolResult(ok=False, error="invalid_arguments", message=str(exc))
        except Exception as exc:  # tool failure must be observable, not crash the loop
            return ToolResult(ok=False, error="tool_error", message=f"{type(exc).__name__}: {exc}")

    def get_material(self, material_id: str) -> ToolResult:
        material = self.materials.get(str(material_id))
        if material is None:
            return ToolResult(
                ok=False,
                error="material_not_found",
                message=f"素材 {material_id!r} 不存在",
            )
        missing = [field for field in REQUIRED_FIELDS if material.get(field) in (None, "")]
        return ToolResult(
            ok=True,
            data={
                "material": material,
                "missing_fields": missing,
            },
        )

    def _missing_fields(self, material: dict[str, Any]) -> list[str]:
        return [field for field in REQUIRED_FIELDS if material.get(field) in (None, "")]

    def check_rules(self, material_id: str) -> ToolResult:
        material = self.materials.get(str(material_id))
        if material is None:
            return ToolResult(
                ok=False,
                error="material_not_found",
                message=f"素材 {material_id!r} 不存在",
            )
        missing = self._missing_fields(material)
        if missing:
            return ToolResult(
                ok=False,
                error="missing_fields",
                message="素材信息不完整，不能执行规则检查",
                data={"missing_fields": missing},
            )

        risks: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        for rule in self.rules:
            rtype = rule.get("type")
            severity = rule.get("severity", "blocking")
            if rtype == "equals":
                field = rule["field"]
                actual = material.get(field)
                if actual != rule["value"]:
                    item = {
                        "rule_id": rule["rule_id"],
                        "severity": severity,
                        "field": field,
                        "reason": f"{rule['name']}：当前值 {actual!r}，要求 {rule['value']!r}",
                        "evidence": f"{field}={actual}",
                    }
                    (risks if severity == "blocking" else warnings).append(item)
            elif rtype == "banned_terms":
                parts = [str(material.get("title", "")), str(material.get("description", ""))]
                parts.extend(str(tag) for tag in material.get("tags", []))
                text = " ".join(parts)
                matched = [term for term in rule.get("terms", []) if term in text]
                if matched:
                    item = {
                        "rule_id": rule["rule_id"],
                        "severity": severity,
                        "field": rule.get("field", "text"),
                        "reason": f"{rule['name']}：命中 {', '.join(matched)}",
                        "evidence": matched,
                    }
                    (risks if severity == "blocking" else warnings).append(item)
            elif rtype == "max_by_platform":
                platform = material.get("platform")
                limit = rule.get("limits", {}).get(platform)
                duration = material.get("duration_sec")
                if limit is not None and isinstance(duration, (int, float)) and duration > limit:
                    item = {
                        "rule_id": rule["rule_id"],
                        "severity": severity,
                        "field": "duration_sec",
                        "reason": f"{rule['name']}：{platform} 上限 {limit} 秒，素材 {duration} 秒",
                        "evidence": f"duration_sec={duration} > {limit}",
                    }
                    (risks if severity == "blocking" else warnings).append(item)
            elif rtype == "min_resolution":
                actual = str(material.get("resolution", ""))
                if material.get("content_type") == "video" and _resolution_rank(actual) < _resolution_rank(rule.get("min", "1080p")):
                    item = {
                        "rule_id": rule["rule_id"],
                        "severity": severity,
                        "field": "resolution",
                        "reason": f"{rule['name']}：当前 {actual}，建议 {rule.get('min')}",
                        "evidence": f"resolution={actual}",
                    }
                    (risks if severity == "blocking" else warnings).append(item)

        blocked = any(item["severity"] == "blocking" for item in risks)
        return ToolResult(
            ok=True,
            data={
                "material_id": material_id,
                "blocked": blocked,
                "risks": risks,
                "warnings": warnings,
                "checked_at": utc_now(),
            },
        )

    def _occupied_for(self, date: str, platform: str) -> list[dict[str, Any]]:
        occupied = [
            item
            for item in self.schedule.get("occupied", [])
            if item.get("date") == date and item.get("platform") == platform
        ]
        occupied.extend(
            item
            for item in self.reservations
            if item.get("date") == date and item.get("platform") == platform
        )
        return occupied

    def reset_reservations(self) -> None:
        self.reservations = []

    def _slot_available(
        self,
        date: str,
        platform: str,
        start_min: int,
        duration_min: int,
        occupied: list[dict[str, Any]] | None = None,
    ) -> bool:
        occupied = occupied if occupied is not None else self._occupied_for(date, platform)
        end_min = start_min + duration_min
        for item in occupied:
            if _overlap(start_min, end_min, _parse_hhmm(item["start"]), _parse_hhmm(item["end"])):
                return False
        return True

    def query_slots(
        self,
        date: str,
        platform: str,
        duration_sec: int,
        preferred_time: str,
    ) -> ToolResult:
        _validate_date(date)
        if not isinstance(platform, str) or not platform:
            raise ValueError("platform 不能为空")
        if not isinstance(duration_sec, int) or duration_sec <= 0:
            raise ValueError("duration_sec 必须是正整数")
        duration_min = max(1, math.ceil(duration_sec / 60))
        start_min = _parse_hhmm(preferred_time)
        day_start = _parse_hhmm(self.schedule.get("day_start", "09:00"))
        day_end = _parse_hhmm(self.schedule.get("day_end", "22:00"))
        step = int(self.schedule.get("slot_minutes", 30))
        occupied = self._occupied_for(date, platform)

        requested_available = self._slot_available(
            date, platform, start_min, duration_min, occupied
        )
        conflicts = [
            {
                "start": item["start"],
                "end": item["end"],
                "material_id": item.get("material_id"),
                "title": item.get("title", ""),
            }
            for item in occupied
            if _overlap(
                start_min,
                start_min + duration_min,
                _parse_hhmm(item["start"]),
                _parse_hhmm(item["end"]),
            )
        ]

        alternatives: list[dict[str, str]] = []
        if not requested_available:
            for candidate in range(day_start, day_end - duration_min + 1, step):
                if candidate == start_min:
                    continue
                if self._slot_available(date, platform, candidate, duration_min, occupied):
                    alternatives.append(
                        {
                            "start": _fmt_hhmm(candidate),
                            "end": _fmt_hhmm(candidate + duration_min),
                        }
                    )
            # Prefer a later slot over an earlier one: moving a publish earlier
            # can be operationally unsafe, while a later slot is a normal fallback.
            alternatives.sort(
                key=lambda item: (
                    0 if _parse_hhmm(item["start"]) >= start_min else 1,
                    abs(_parse_hhmm(item["start"]) - start_min),
                )
            )
            alternatives = alternatives[:6]

        nearest = preferred_time if requested_available else (alternatives[0]["start"] if alternatives else None)
        return ToolResult(
            ok=True,
            data={
                "date": date,
                "platform": platform,
                "duration_sec": duration_sec,
                "duration_min": duration_min,
                "timezone": self.schedule.get("timezone", "Asia/Shanghai"),
                "requested": {
                    "start": preferred_time,
                    "end": _fmt_hhmm(start_min + duration_min),
                    "available": requested_available,
                },
                "conflicts": conflicts,
                "alternatives": alternatives,
                "nearest_alternative": nearest,
            },
        )

    def save_draft(
        self,
        material_id: str,
        date: str,
        slot: str,
        requested_time: str,
        draft_status: str,
        risk_summary: list[dict[str, Any]] | None = None,
        pending_items: list[str] | None = None,
        note: str = "",
    ) -> ToolResult:
        material = self.materials.get(str(material_id))
        if material is None:
            return ToolResult(
                ok=False,
                error="material_not_found",
                message=f"素材 {material_id!r} 不存在",
            )
        _validate_date(date)
        slot_min = _parse_hhmm(slot)
        _parse_hhmm(requested_time)
        if draft_status not in ("ready", "pending_confirmation"):
            raise ValueError("draft_status 只能是 ready 或 pending_confirmation")
        if risk_summary is None:
            risk_summary = []
        elif isinstance(risk_summary, dict):
            risk_summary = [risk_summary]
        elif isinstance(risk_summary, str):
            risk_summary = [{"note": risk_summary}]
        elif not isinstance(risk_summary, list):
            risk_summary = [{"note": str(risk_summary)}]

        if isinstance(pending_items, str):
            pending_items = [pending_items]
        pending_items = [str(item) for item in (pending_items or [])]
        if draft_status == "pending_confirmation" and not pending_items:
            raise ValueError("pending_confirmation 草稿必须提供 pending_items")
        if slot != requested_time and draft_status == "ready":
            return ToolResult(
                ok=False,
                error="confirmation_required",
                message="建议时段与原要求不同，draft_status 必须为 pending_confirmation",
            )

        platform = material.get("platform")
        duration_sec = material.get("duration_sec")
        if not platform or not isinstance(duration_sec, int):
            return ToolResult(
                ok=False,
                error="material_incomplete",
                message="素材缺少 platform 或 duration_sec，不能保存草稿",
            )
        duration_min = max(1, math.ceil(duration_sec / 60))

        # Idempotency must be checked before the slot reservation check:
        # a repeated save would otherwise look like a conflict with itself.
        key = self.store.make_key(material_id, date, slot, requested_time, draft_status)
        for existing in self.store.list_drafts():
            if existing.get("idempotency_key") == key:
                return ToolResult(
                    ok=True,
                    data={
                        "draft_id": existing["draft_id"],
                        "duplicate": True,
                        "draft": existing,
                    },
                )

        if not self._slot_available(date, platform, slot_min, duration_min):
            return ToolResult(
                ok=False,
                error="slot_conflict",
                message=f"{date} {slot} 在 {platform} 已被占用，不能保存草稿",
            )

        draft = {
            "material_id": material_id,
            "date": date,
            "slot": slot,
            "requested_time": requested_time,
            "draft_status": draft_status,
            "risk_summary": risk_summary or [],
            "pending_items": pending_items,
            "note": note,
        }
        try:
            saved = self.store.save(draft)
        except StorageError as exc:
            return ToolResult(
                ok=False,
                error="storage_unavailable",
                message=str(exc),
            )
        if not saved["duplicate"]:
            self.reservations.append(
                {
                    "date": date,
                    "platform": platform,
                    "start": slot,
                    "end": _fmt_hhmm(slot_min + duration_min),
                    "material_id": material_id,
                    "draft_id": saved["draft_id"],
                    "title": "本批次草稿占位",
                }
            )
        return ToolResult(
            ok=True,
            data={
                "draft_id": saved["draft_id"],
                "duplicate": saved["duplicate"],
                "draft": saved["draft"],
            },
        )

    def specs(self) -> str:
        return TOOL_SPECS
