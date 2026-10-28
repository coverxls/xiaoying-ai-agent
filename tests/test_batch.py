from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.batch import BatchRunner, normalize_batch_input
from src.agent.mock_scripts import MOCK_SCENARIOS, ScriptedLLM
from src.agent.store import JsonDraftStore
from src.agent.tools import ToolRegistry


M08_AFTER_RESERVATION = [
    {
        "action": "get_material",
        "args": {"material_id": "M08"},
        "decision_basis": "读取第 2 条素材。",
    },
    {
        "action": "check_rules",
        "args": {"material_id": "M08"},
        "decision_basis": "检查规则。",
    },
    {
        "action": "query_slots",
        "args": {
            "date": "2026-11-02",
            "platform": "douyin",
            "duration_sec": 30,
            "preferred_time": "10:00",
        },
        "decision_basis": "第 1 条已占用 10:00，查询剩余时段。",
    },
    {
        "action": "save_draft",
        "args": {
            "material_id": "M08",
            "date": "2026-11-02",
            "slot": "10:30",
            "requested_time": "10:00",
            "draft_status": "pending_confirmation",
            "risk_summary": [],
            "pending_items": ["是否接受 10:30 替代 10:00"],
            "note": "批次内 10:00 已被 M06 占用，后移到 10:30。",
        },
        "decision_basis": "10:00 冲突，10:30 可用，保存待确认草稿。",
    },
    {
        "action": "finish",
        "args": {
            "status": "pending_confirmation",
            "message": "10:00 已被同批次 M06 占用，建议 10:30；已保存待确认草稿，未发布。",
            "pending_items": ["是否接受 10:30 替代 10:00"],
        },
        "decision_basis": "草稿保存成功，但改时段需要用户确认。",
    },
]


def make_runner(tmp_path, scripts, fail_save=False):
    store = JsonDraftStore(tmp_path / "drafts.json", fail_save=fail_save)
    tools = ToolRegistry(store=store)
    runner = BatchRunner(
        llm_factory=lambda item: ScriptedLLM(scripts[item["material_id"]]),
        tools=tools,
        max_steps=8,
        trace_dir=tmp_path / "traces",
    )
    return runner, store


def test_batch_avoids_intra_batch_slot_conflict(tmp_path):
    runner, store = make_runner(
        tmp_path,
        {
            "M06": MOCK_SCENARIOS["normal"],
            "M08": M08_AFTER_RESERVATION,
        },
    )
    payload = runner.run(
        {
            "materials": [
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                },
                {
                    "material_id": "M08",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                },
            ]
        },
        batch_trace_path=tmp_path / "batch.json",
    )

    assert payload["status"] == "pending_confirmation"
    assert payload["total"] == 2
    assert payload["counts"]["completed"] == 1
    assert payload["counts"]["pending_confirmation"] == 1
    assert payload["results"][0]["suggested_slot"]["start"] == "10:00"
    assert payload["results"][1]["suggested_slot"]["start"] == "10:30"
    assert payload["results"][1]["draft"]["draft_status"] == "pending_confirmation"

    drafts = store.list_drafts()
    assert len(drafts) == 2
    assert {draft["slot"] for draft in drafts} == {"10:00", "10:30"}
    assert Path(payload["trace_path"]).exists()
    assert Path(payload["results"][0]["trace_path"]).exists()


def test_batch_continues_after_missing_and_blocked(tmp_path):
    runner, store = make_runner(
        tmp_path,
        {
            "M02": MOCK_SCENARIOS["missing"],
            "M03": MOCK_SCENARIOS["risk"],
            "M06": MOCK_SCENARIOS["normal"],
        },
    )
    payload = runner.run(
        {
            "materials": [
                {
                    "material_id": "M02",
                    "requested_publish_at": "2026-11-03T12:00:00+08:00",
                },
                {
                    "material_id": "M03",
                    "requested_publish_at": "2026-11-01T20:00:00+08:00",
                },
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                },
            ]
        }
    )

    assert payload["status"] == "partial"
    assert payload["counts"]["needs_input"] == 1
    assert payload["counts"]["blocked"] == 1
    assert payload["counts"]["completed"] == 1
    assert [item["status"] for item in payload["results"]] == [
        "needs_input",
        "blocked",
        "completed",
    ]
    assert len(store.list_drafts()) == 1


def test_batch_skips_duplicate_material_without_second_draft(tmp_path):
    runner, store = make_runner(tmp_path, {"M06": MOCK_SCENARIOS["normal"]})
    payload = runner.run(
        {
            "materials": [
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                },
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                },
            ]
        }
    )

    assert payload["total"] == 2
    assert payload["status"] == "completed"
    assert payload["results"][0]["status"] == "completed"
    assert payload["results"][1]["status"] == "skipped_duplicate"
    assert payload["results"][1]["steps"] == 0
    assert payload["results"][1]["draft"]["draft_id"] == payload["results"][0]["draft"]["draft_id"]
    assert payload["counts"]["skipped_duplicate"] == 1
    assert len(store.list_drafts()) == 1


def test_batch_save_failure_is_not_success(tmp_path):
    runner, store = make_runner(
        tmp_path,
        {"M06": MOCK_SCENARIOS["save_failure"]},
        fail_save=True,
    )
    payload = runner.run(
        {
            "materials": [
                {
                    "material_id": "M06",
                    "requested_publish_at": "2026-11-02T10:00:00+08:00",
                }
            ]
        }
    )
    assert payload["status"] == "save_failed"
    assert payload["results"][0]["status"] == "save_failed"
    assert payload["results"][0]["draft"]["ok"] is False
    assert store.list_drafts() == []


def test_normalize_batch_input_limits_and_shape():
    with pytest.raises(ValueError):
        normalize_batch_input([])
    with pytest.raises(ValueError):
        normalize_batch_input([{"material_id": f"M{i:02d}"} for i in range(1, 22)])
    items = normalize_batch_input({"materials": [{"material_id": "M06"}]})
    assert items == [{"material_id": "M06"}]
