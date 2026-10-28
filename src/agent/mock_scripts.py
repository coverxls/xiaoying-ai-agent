from __future__ import annotations

import json
from typing import Any


def _finish(status: str, message: str, pending_items: list[str] | None = None) -> dict[str, Any]:
    return {
        "action": "finish",
        "args": {
            "status": status,
            "message": message,
            "pending_items": pending_items or [],
        },
        "decision_basis": "根据当前状态结束本次处理。",
    }


MOCK_SCENARIOS: dict[str, list[dict[str, Any]]] = {
    "normal": [
        {
            "action": "get_material",
            "args": {"material_id": "M06"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M06"},
            "decision_basis": "信息完整，检查版权、敏感词和平台时长。",
        },
        {
            "action": "query_slots",
            "args": {
                "date": "2026-11-02",
                "platform": "douyin",
                "duration_sec": 30,
                "preferred_time": "10:00",
            },
            "decision_basis": "规则通过，查询用户要求的 10:00 是否可用。",
        },
        {
            "action": "save_draft",
            "args": {
                "material_id": "M06",
                "date": "2026-11-02",
                "slot": "10:00",
                "requested_time": "10:00",
                "draft_status": "ready",
                "risk_summary": [],
                "pending_items": [],
                "note": "规则通过，原时段可用。",
            },
            "decision_basis": "原时段可用，保存草稿。",
        },
        _finish(
            "completed",
            "素材审核通过，已保存 2026-11-02 10:00 的发布草稿；未发布。",
        ),
    ],
    "missing": [
        {
            "action": "get_material",
            "args": {"material_id": "M02"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        _finish(
            "needs_input",
            "素材信息不完整，暂不能排期；请补充缺失字段后重试。",
            ["请补充 duration_sec（视频时长，秒）", "请补充 rights_status（版权状态）"],
        ),
    ],
    "risk": [
        {
            "action": "get_material",
            "args": {"material_id": "M03"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M03"},
            "decision_basis": "信息完整，检查版权、敏感词和平台时长。",
        },
        _finish(
            "blocked",
            "规则检查未通过，已阻断排期；未保存草稿、未发布。",
            ["版权状态为 pending，需先完成版权确认"],
        ),
    ],
    "conflict": [
        {
            "action": "get_material",
            "args": {"material_id": "M01"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M01"},
            "decision_basis": "信息完整，检查版权、敏感词和平台时长。",
        },
        {
            "action": "query_slots",
            "args": {
                "date": "2026-11-01",
                "platform": "douyin",
                "duration_sec": 30,
                "preferred_time": "18:00",
            },
            "decision_basis": "规则通过，查询用户硬性要求的 18:00 是否可用。",
        },
        {
            "action": "save_draft",
            "args": {
                "material_id": "M01",
                "date": "2026-11-01",
                "slot": "19:00",
                "requested_time": "18:00",
                "draft_status": "pending_confirmation",
                "risk_summary": [],
                "pending_items": ["是否接受 19:00 替代原要求 18:00（硬性要求）"],
                "note": "18:00 已被占用，19:00 为最近可用时段；保存为待确认草稿。",
            },
            "decision_basis": "18:00 冲突，19:00 是最近可用时段；不能擅自改硬性要求，保存待确认草稿。",
        },
        _finish(
            "pending_confirmation",
            "18:00 已被占用，建议 19:00；已保存待确认草稿，未发布。",
            ["是否接受 19:00 替代原要求 18:00（硬性要求）"],
        ),
    ],
    "duplicate": [
        {
            "action": "get_material",
            "args": {"material_id": "M06"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M06"},
            "decision_basis": "信息完整，检查版权、敏感词和平台时长。",
        },
        {
            "action": "query_slots",
            "args": {
                "date": "2026-11-02",
                "platform": "douyin",
                "duration_sec": 30,
                "preferred_time": "10:00",
            },
            "decision_basis": "规则通过，查询用户要求的 10:00 是否可用。",
        },
        {
            "action": "save_draft",
            "args": {
                "material_id": "M06",
                "date": "2026-11-02",
                "slot": "10:00",
                "requested_time": "10:00",
                "draft_status": "ready",
                "risk_summary": [],
                "pending_items": [],
                "note": "重复提交，尝试保存草稿。",
            },
            "decision_basis": "原时段可用，尝试保存草稿；若已存在则复用。",
        },
        _finish(
            "completed",
            "检测到重复提交，已复用已有草稿；未重复创建、未发布。",
        ),
    ],
    "save_failure": [
        {
            "action": "get_material",
            "args": {"material_id": "M06"},
            "decision_basis": "先读取素材元数据，确认信息是否完整。",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M06"},
            "decision_basis": "信息完整，检查版权、敏感词和平台时长。",
        },
        {
            "action": "query_slots",
            "args": {
                "date": "2026-11-02",
                "platform": "douyin",
                "duration_sec": 30,
                "preferred_time": "10:00",
            },
            "decision_basis": "规则通过，查询用户要求的 10:00 是否可用。",
        },
        {
            "action": "save_draft",
            "args": {
                "material_id": "M06",
                "date": "2026-11-02",
                "slot": "10:00",
                "requested_time": "10:00",
                "draft_status": "ready",
                "risk_summary": [],
                "pending_items": [],
                "note": "尝试保存草稿。",
            },
            "decision_basis": "原时段可用，保存草稿。",
        },
        _finish(
            "save_failed",
            "草稿保存失败：storage_unavailable；未保存、未发布。",
        ),
    ],
}


class ScenarioMockLLM:
    """Deterministic mock. It validates the loop, not real model quality."""

    def __init__(self, scenario: str = "normal"):
        if scenario not in MOCK_SCENARIOS:
            raise ValueError(f"unknown mock scenario {scenario!r}")
        self.name = "mock"
        self.scenario = scenario
        self.actions = list(MOCK_SCENARIOS[scenario])
        self.index = 0
        self.model = f"mock:{scenario}"

    def complete(self, messages: list[dict[str, str]]) -> str:
        if self.index >= len(self.actions):
            return json.dumps(
                _finish("blocked", "Mock 脚本已结束，未继续执行。"),
                ensure_ascii=False,
            )
        action = self.actions[self.index]
        self.index += 1
        return json.dumps(action, ensure_ascii=False)


def _extract_state(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Read the state summary that the prompt already exposes to the model."""
    markers = ["更新后的状态：", "## 当前状态"]
    for message in reversed(messages):
        text = message.get("content", "")
        for marker in markers:
            idx = text.rfind(marker)
            if idx >= 0:
                start = text.find("{", idx)
                if start >= 0:
                    try:
                        value, _ = json.JSONDecoder().raw_decode(text[start:])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        pass
    raise ValueError("mock could not find state in prompt")


class PolicyMockLLM:
    """Deterministic policy mock for batch/offline runs.

    It follows the same branch order as the prompt, but it is still a mock:
    it proves the loop and state machine, not real-model decision quality.
    """

    name = "mock"
    model = "mock:policy"

    def complete(self, messages: list[dict[str, str]]) -> str:
        state = _extract_state(messages)
        material = state.get("material") or {}
        material_id = state.get("material_id")
        date = state.get("requested_date")
        requested = state.get("requested_time")

        def action(name: str, args: dict[str, Any], basis: str) -> str:
            return json.dumps(
                {"action": name, "args": args, "decision_basis": basis},
                ensure_ascii=False,
            )

        def finish(status: str, message: str, pending: list[str] | None = None) -> str:
            return json.dumps(
                {
                    "action": "finish",
                    "args": {
                        "status": status,
                        "message": message,
                        "pending_items": pending or [],
                    },
                    "decision_basis": "Mock 状态机根据当前 observation 结束。",
                },
                ensure_ascii=False,
            )

        if not state.get("material_loaded"):
            if state.get("material_error"):
                return finish(
                    "needs_input",
                    f"素材 {material_id} 不存在，无法排期。",
                    ["请提供正确的素材 ID"],
                )
            return action(
                "get_material",
                {"material_id": material_id},
                "先读取素材元数据，确认字段是否完整。",
            )

        missing = state.get("missing_fields") or []
        if missing:
            return finish(
                "needs_input",
                f"素材 {material_id} 信息不完整，暂不能排期。",
                [f"请补充 {field}" for field in missing],
            )

        if not state.get("rules_checked"):
            return action(
                "check_rules",
                {"material_id": material_id},
                "信息完整，检查版权、敏感词、平台时长和分辨率。",
            )

        if state.get("blocked"):
            risks = state.get("risks") or []
            reasons = [risk.get("reason", risk.get("rule_id", "")) for risk in risks]
            return finish(
                "blocked",
                f"素材 {material_id} 被规则阻断，未保存草稿。",
                reasons or ["规则检查未通过"],
            )

        if not state.get("slot_checked"):
            return action(
                "query_slots",
                {
                    "date": date,
                    "platform": material.get("platform"),
                    "duration_sec": material.get("duration_sec"),
                    "preferred_time": requested,
                },
                "规则通过，查询用户要求时段的占用情况。",
            )

        if state.get("draft_error"):
            return finish(
                "save_failed",
                f"素材 {material_id} 草稿保存失败，未保存、未发布。",
                ["草稿未保存，请在存储恢复后重试"],
            )

        if state.get("draft_attempted") and state.get("draft"):
            draft = (state.get("draft") or {}).get("draft") or {}
            slot = draft.get("slot") or requested
            if draft.get("draft_status") == "pending_confirmation":
                return finish(
                    "pending_confirmation",
                    f"{requested} 不可用，建议 {slot}；已保存待确认草稿，未发布。",
                    [f"是否接受 {slot} 替代 {requested}"],
                )
            return finish(
                "completed",
                f"素材 {material_id} 审核通过，已保存 {date} {slot} 草稿；未发布。",
            )

        suggested = (state.get("suggested_slot") or {}).get("start")
        if not suggested:
            return finish(
                "blocked",
                f"素材 {material_id} 没有可用时段，未保存草稿。",
                ["请调整发布时间或补充可用时段"],
            )

        changed = suggested != requested
        draft_status = "pending_confirmation" if changed else "ready"
        pending = [f"是否接受 {suggested} 替代 {requested}"] if changed else []
        return action(
            "save_draft",
            {
                "material_id": material_id,
                "date": date,
                "slot": suggested,
                "requested_time": requested,
                "draft_status": draft_status,
                "risk_summary": state.get("risks") or [],
                "pending_items": pending,
                "note": "Mock 批量排期草稿。",
            },
            "规则通过，按查询结果保存草稿；改时段时标记待确认。",
        )


class ScriptedLLM:
    """Fixed action list for unit tests."""

    def __init__(self, actions: list[dict[str, Any]]):
        self.name = "scripted"
        self.actions = list(actions)
        self.index = 0
        self.model = "scripted"

    def complete(self, messages: list[dict[str, str]]) -> str:
        if self.index >= len(self.actions):
            return json.dumps(
                _finish("blocked", "脚本已结束，未继续执行。"),
                ensure_ascii=False,
            )
        action = self.actions[self.index]
        self.index += 1
        return json.dumps(action, ensure_ascii=False)
