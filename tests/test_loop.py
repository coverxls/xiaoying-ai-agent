from __future__ import annotations

import json
from pathlib import Path

from src.agent.loop import AgentLoop
from src.agent.mock_scripts import MOCK_SCENARIOS, ScriptedLLM
from src.agent.store import JsonDraftStore
from src.agent.tools import ToolRegistry


def make_loop(tmp_path, actions, max_steps=8, fail_save=False):
    store = JsonDraftStore(tmp_path / "drafts.json", fail_save=fail_save)
    tools = ToolRegistry(store=store)
    loop = AgentLoop(
        llm=ScriptedLLM(actions),
        tools=tools,
        max_steps=max_steps,
        trace_dir=tmp_path / "traces",
    )
    return loop, store


def run_case(tmp_path, actions, user_input, max_steps=8, fail_save=False):
    loop, store = make_loop(tmp_path, actions, max_steps=max_steps, fail_save=fail_save)
    return loop.run(user_input), store


def test_normal_completed_and_draft_saved(tmp_path):
    result, _ = run_case(
        tmp_path,
        MOCK_SCENARIOS["normal"],
        {
            "material_id": "M06",
            "requested_publish_at": "2026-11-02T10:00:00+08:00",
        },
    )
    assert result.status == "completed"
    assert result.draft and result.draft["ok"] is True
    assert result.draft["draft_status"] == "ready"
    assert result.published is False
    assert "已发布" not in result.message
    assert Path(result.trace_path).exists()


def test_missing_info_stops_without_save(tmp_path):
    result, store = run_case(
        tmp_path,
        MOCK_SCENARIOS["missing"],
        {
            "material_id": "M02",
            "requested_publish_at": "2026-11-03T12:00:00+08:00",
        },
    )
    assert result.status == "needs_input"
    assert result.draft is None
    assert result.pending_items
    assert result.steps == 2
    assert store.list_drafts() == []


def test_rule_risk_blocks_before_slot_and_save(tmp_path):
    result, store = run_case(
        tmp_path,
        MOCK_SCENARIOS["risk"],
        {
            "material_id": "M03",
            "requested_publish_at": "2026-11-01T20:00:00+08:00",
        },
    )
    assert result.status == "blocked"
    assert result.audit["risks"]
    assert result.draft is None
    assert result.steps == 3
    assert store.list_drafts() == []


def test_conflict_requires_confirmation_and_keeps_original_time(tmp_path):
    result, _ = run_case(
        tmp_path,
        MOCK_SCENARIOS["conflict"],
        {
            "material_id": "M01",
            "requested_publish_at": "2026-11-01T18:00:00+08:00",
        },
    )
    assert result.status == "pending_confirmation"
    assert result.suggested_slot["start"] == "19:00"
    assert result.suggested_slot["requested_time"] == "18:00"
    assert result.suggested_slot["changed_from_request"] is True
    assert result.draft and result.draft["draft_status"] == "pending_confirmation"
    assert result.pending_items
    assert result.published is False
    assert "已发布" not in result.message


def test_duplicate_submission_reuses_one_draft(tmp_path):
    store = JsonDraftStore(tmp_path / "drafts.json")
    tools = ToolRegistry(store=store)
    results = []
    for _ in range(2):
        loop = AgentLoop(
            llm=ScriptedLLM(MOCK_SCENARIOS["duplicate"]),
            tools=tools,
            max_steps=8,
            trace_dir=tmp_path / "traces",
        )
        results.append(
            loop.run(
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                }
            )
        )
    assert results[0].draft["duplicate"] is False
    assert results[1].draft["duplicate"] is True
    assert results[0].draft["draft_id"] == results[1].draft["draft_id"]
    assert len(store.list_drafts()) == 1


def test_duplicate_conflict_proposal_reuses_one_pending_draft(tmp_path):
    store = JsonDraftStore(tmp_path / "drafts.json")
    tools = ToolRegistry(store=store)
    results = []
    for _ in range(2):
        loop = AgentLoop(
            llm=ScriptedLLM(MOCK_SCENARIOS["conflict"]),
            tools=tools,
            max_steps=8,
            trace_dir=tmp_path / "traces",
        )
        results.append(
            loop.run(
                {
                    "material_id": "M01",
                    "requested_publish_at": "2026-11-01T18:00:00+08:00",
                }
            )
        )
    assert results[0].status == "pending_confirmation"
    assert results[1].status == "pending_confirmation"
    assert results[0].draft["duplicate"] is False
    assert results[1].draft["duplicate"] is True
    assert results[0].draft["draft_id"] == results[1].draft["draft_id"]
    assert len(store.list_drafts()) == 1
    assert store.list_drafts()[0]["draft_status"] == "pending_confirmation"


def test_save_failure_is_not_reported_as_success(tmp_path):
    result, store = run_case(
        tmp_path,
        MOCK_SCENARIOS["save_failure"],
        {
            "material_id": "M06",
            "requested_publish_at": "2026-11-02T10:00:00+08:00",
        },
        fail_save=True,
    )
    assert result.status == "save_failed"
    assert result.draft and result.draft["ok"] is False
    assert result.draft["error"] == "storage_unavailable"
    assert "失败" in result.message
    assert store.list_drafts() == []


def test_max_steps_returns_explicit_incomplete(tmp_path):
    actions = [
        {
            "action": "get_material",
            "args": {"material_id": "M06"},
            "decision_basis": "keep reading",
        }
    ] * 3
    result, _ = run_case(
        tmp_path,
        actions,
        {
            "material_id": "M06",
            "requested_publish_at": "2026-11-02T10:00:00+08:00",
        },
        max_steps=2,
    )
    assert result.status == "max_steps"
    assert result.steps == 2
    assert "未完成" in result.message


def test_finish_before_material_is_rejected_then_recovers(tmp_path):
    actions = [
        {
            "action": "finish",
            "args": {"status": "completed", "message": "done"},
            "decision_basis": "too early",
        },
        *MOCK_SCENARIOS["normal"],
    ]
    result, _ = run_case(
        tmp_path,
        actions,
        {
            "material_id": "M06",
            "requested_publish_at": "2026-11-02T10:00:00+08:00",
        },
    )
    assert result.status == "completed"
    assert result.steps == 6
    assert any(
        step.observation.get("error") == "finish_rejected" for step in result_steps(tmp_path)
    )


def result_steps(tmp_path):
    trace_files = sorted((tmp_path / "traces").glob("*.json"))
    assert trace_files
    trace = json.loads(trace_files[-1].read_text(encoding="utf-8"))
    from src.agent.models import StepRecord

    return [StepRecord(**step) for step in trace["steps"]]


def test_alternative_cannot_be_completed_without_confirmation(tmp_path):
    actions = [
        {
            "action": "get_material",
            "args": {"material_id": "M01"},
            "decision_basis": "read",
        },
        {
            "action": "check_rules",
            "args": {"material_id": "M01"},
            "decision_basis": "check",
        },
        {
            "action": "query_slots",
            "args": {
                "date": "2026-11-01",
                "platform": "douyin",
                "duration_sec": 30,
                "preferred_time": "18:00",
            },
            "decision_basis": "query",
        },
        {
            "action": "save_draft",
            "args": {
                "material_id": "M01",
                "date": "2026-11-01",
                "slot": "19:00",
                "requested_time": "18:00",
                "draft_status": "pending_confirmation",
                "pending_items": ["是否接受 19:00 替代 18:00"],
            },
            "decision_basis": "save proposal",
        },
        {
            "action": "finish",
            "args": {"status": "completed", "message": "done"},
            "decision_basis": "wrong status",
        },
        {
            "action": "finish",
            "args": {
                "status": "pending_confirmation",
                "message": "saved draft",
                "pending_items": ["是否接受 19:00 替代 18:00"],
            },
            "decision_basis": "correct status",
        },
    ]
    result, _ = run_case(
        tmp_path,
        actions,
        {
            "material_id": "M01",
            "requested_publish_at": "2026-11-01T18:00:00+08:00",
        },
    )
    assert result.status == "pending_confirmation"
    assert result.steps == 6


def test_publish_claim_is_rejected(tmp_path):
    actions = [
        *MOCK_SCENARIOS["normal"][:4],
        {
            "action": "finish",
            "args": {"status": "completed", "message": "已发布"},
            "decision_basis": "wrong",
        },
        {
            "action": "finish",
            "args": {"status": "completed", "message": "已保存草稿，未发布。"},
            "decision_basis": "correct",
        },
    ]
    result, _ = run_case(
        tmp_path,
        actions,
        {
            "material_id": "M06",
            "requested_publish_at": "2026-11-02T10:00:00+08:00",
        },
    )
    assert result.status == "completed"
    assert "已发布" not in result.message


def test_material_not_found_can_request_correct_id(tmp_path):
    actions = [
        {
            "action": "get_material",
            "args": {"material_id": "M99"},
            "decision_basis": "read",
        },
        {
            "action": "finish",
            "args": {
                "status": "needs_input",
                "message": "素材不存在",
                "pending_items": ["请提供正确的素材 ID"],
            },
            "decision_basis": "ask",
        },
    ]
    result, _ = run_case(tmp_path, actions, {"material_id": "M99"})
    assert result.status == "needs_input"
    assert result.pending_items
