from __future__ import annotations

import pytest

from src.agent.store import JsonDraftStore
from src.agent.tools import ToolRegistry


@pytest.fixture()
def tools(tmp_path):
    store = JsonDraftStore(tmp_path / "drafts.json")
    return ToolRegistry(store=store)


def test_get_material_reports_missing_fields(tools):
    result = tools.get_material("M02")
    assert result.ok
    assert "duration_sec" in result.data["missing_fields"]
    assert "rights_status" in result.data["missing_fields"]


def test_check_rules_blocks_pending_rights(tools):
    result = tools.check_rules("M03")
    assert result.ok
    assert result.data["blocked"] is True
    assert any(risk["rule_id"] == "R001" for risk in result.data["risks"])


def test_check_rules_blocks_banned_terms(tools):
    result = tools.check_rules("M04")
    assert result.ok
    assert result.data["blocked"] is True
    assert any(risk["rule_id"] == "R002" for risk in result.data["risks"])


def test_check_rules_blocks_platform_duration(tools):
    result = tools.check_rules("M05")
    assert result.ok
    assert result.data["blocked"] is True
    assert any(risk["rule_id"] == "R003" for risk in result.data["risks"])


def test_query_slots_finds_conflict_and_alternative(tools):
    result = tools.query_slots("2026-11-01", "douyin", 30, "18:00")
    assert result.ok
    assert result.data["requested"]["available"] is False
    assert result.data["conflicts"]
    assert result.data["nearest_alternative"] == "19:00"


def test_query_slots_reports_available(tools):
    result = tools.query_slots("2026-11-02", "douyin", 30, "10:00")
    assert result.ok
    assert result.data["requested"]["available"] is True
    assert result.data["nearest_alternative"] == "10:00"


def test_save_draft_is_idempotent(tools):
    kwargs = dict(
        material_id="M06",
        date="2026-11-02",
        slot="10:00",
        requested_time="10:00",
        draft_status="ready",
        risk_summary=[],
        pending_items=[],
        note="first",
    )
    first = tools.save_draft(**kwargs)
    second = tools.save_draft(**kwargs)
    assert first.ok and second.ok
    assert first.data["duplicate"] is False
    assert second.data["duplicate"] is True
    assert first.data["draft_id"] == second.data["draft_id"]
    assert len(tools.store.list_drafts()) == 1


def test_save_draft_rejects_ready_alternative(tools):
    result = tools.save_draft(
        material_id="M01",
        date="2026-11-01",
        slot="19:00",
        requested_time="18:00",
        draft_status="ready",
        pending_items=[],
    )
    assert result.ok is False
    assert result.error == "confirmation_required"


def test_save_draft_rejects_occupied_slot(tools):
    result = tools.save_draft(
        material_id="M01",
        date="2026-11-01",
        slot="18:00",
        requested_time="18:00",
        draft_status="ready",
        pending_items=[],
    )
    assert result.ok is False
    assert result.error == "slot_conflict"


def test_save_draft_failure_is_explicit(tmp_path):
    store = JsonDraftStore(tmp_path / "drafts.json", fail_save=True)
    tools = ToolRegistry(store=store)
    result = tools.save_draft(
        material_id="M06",
        date="2026-11-02",
        slot="10:00",
        requested_time="10:00",
        draft_status="ready",
        pending_items=[],
    )
    assert result.ok is False
    assert result.error == "storage_unavailable"


def test_invalid_time_is_observed_as_tool_error(tools):
    result = tools.call(
        "query_slots",
        {
            "date": "2026-11-02",
            "platform": "douyin",
            "duration_sec": 30,
            "preferred_time": "25:99",
        },
    )
    assert result.ok is False
    assert result.error == "invalid_arguments"
