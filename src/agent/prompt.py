from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .tools import TOOL_SPECS


SYSTEM_PROMPT = f"""\
你是“素材审核与发布排期 Agent”，服务内容团队。你的职责是审核素材并生成可确认的发布草稿，不是发布内容。

## 硬性规则
1. 只能保存草稿。绝不能发布，也不能在 message 中说“已发布”“发布成功”。最终输出必须 published=false。
2. 不得擅自改变用户的硬性发布时间。若建议时段与原要求不同，草稿必须 draft_status=pending_confirmation，最终 status=pending_confirmation，pending_items 必须包含“是否接受 HH:MM 替代 HH:MM”。
3. 第一步必须调用 get_material。若 missing_fields 非空，直接 finish(status=needs_input)，列出缺失字段和需要用户补充的问题；不要猜。
4. 信息完整后调用 check_rules。若 blocked=true，直接 finish(status=blocked)，列出 risks；不要查时段、不要保存。
5. 规则通过后调用 query_slots。requested.available=true 时使用原时段；否则从 alternatives 中选择最近的可用时段。不要编造时段。
6. 保存草稿调用 save_draft。若返回 ok=false，只能 finish(status=save_failed)，不得声称保存成功；可以重试一次。
7. 若 save_draft 返回 duplicate=true，复用已有 draft_id，不要重复创建。
8. 最多 8 步；每步只做一个动作。达到上限时系统会返回 max_steps。
9. 只输出一个 JSON 对象，不要 Markdown、不要代码围栏。

## 输出格式
{{"action":"工具名或 finish","args":{{...}},"decision_basis":"一句话说明为什么做这个动作"}}

## 可用工具
{TOOL_SPECS}

## finish 的 args
{{"status":"completed|pending_confirmation|needs_input|blocked|save_failed","message":"给用户看的中文结论","pending_items":["..."]}}

状态含义：
- completed：原时段可用，草稿已保存。
- pending_confirmation：需要用户确认替代时段或风险，草稿已保存为待确认。
- needs_input：素材信息缺失，未保存。
- blocked：规则阻断，未保存。
- save_failed：保存失败，未保存。

## 当前状态
{{state}}
"""


def split_iso(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.date().isoformat(), dt.strftime("%H:%M")
    except ValueError:
        return None, None


def initial_state(user_input: dict[str, Any]) -> dict[str, Any]:
    date, time = split_iso(user_input.get("requested_publish_at"))
    if user_input.get("requested_date"):
        date = user_input["requested_date"]
    if user_input.get("requested_time"):
        time = user_input["requested_time"]
    return {
        "material_id": user_input.get("material_id"),
        "requested_publish_at": user_input.get("requested_publish_at"),
        "requested_date": date,
        "requested_time": time,
        "hard_requirement": None,
        "notes": user_input.get("notes", ""),
        "material_loaded": False,
        "material": None,
        "missing_fields": [],
        "rules_checked": False,
        "blocked": False,
        "risks": [],
        "warnings": [],
        "slot_checked": False,
        "slot_result": None,
        "suggested_slot": None,
        "suggested_time": None,
        "draft_attempted": False,
        "draft": None,
        "draft_error": None,
        "duplicate": False,
    }


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    """A compact state view for the model; never includes hidden chain-of-thought."""
    material = state.get("material") or {}
    return {
        "material_id": state.get("material_id"),
        "requested_publish_at": state.get("requested_publish_at"),
        "requested_date": state.get("requested_date"),
        "requested_time": state.get("requested_time"),
        "hard_requirement": state.get("hard_requirement"),
        "material_loaded": state.get("material_loaded"),
        "missing_fields": state.get("missing_fields", []),
        "material": {
            "title": material.get("title"),
            "content_type": material.get("content_type"),
            "duration_sec": material.get("duration_sec"),
            "platform": material.get("platform"),
            "rights_status": material.get("rights_status"),
            "resolution": material.get("resolution"),
        }
        if material
        else None,
        "rules_checked": state.get("rules_checked"),
        "blocked": state.get("blocked"),
        "risks": state.get("risks", []),
        "warnings": state.get("warnings", []),
        "slot_checked": state.get("slot_checked"),
        "slot_result": state.get("slot_result"),
        "suggested_slot": state.get("suggested_slot"),
        "draft_attempted": state.get("draft_attempted"),
        "draft": state.get("draft"),
        "draft_error": state.get("draft_error"),
        "duplicate": state.get("duplicate"),
    }


def initial_messages(user_input: dict[str, Any], state: dict[str, Any]) -> list[dict[str, str]]:
    system = SYSTEM_PROMPT.replace("{state}", json.dumps(state_summary(state), ensure_ascii=False, indent=2))
    user = (
        "用户请求：\n"
        + json.dumps(user_input, ensure_ascii=False, indent=2)
        + "\n\n请决定下一步：只输出一个 JSON 动作。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def observation_message(
    step: int,
    tool_name: str,
    result: dict[str, Any],
    state: dict[str, Any],
) -> str:
    observation = json.dumps(result, ensure_ascii=False, indent=2)
    if len(observation) > 5000:
        observation = observation[:5000] + "\n...（已截断）"
    return (
        f"第 {step} 步，工具 `{tool_name}` 返回：\n{observation}\n\n"
        f"更新后的状态：\n{json.dumps(state_summary(state), ensure_ascii=False, indent=2)}\n\n"
        "请决定下一步：继续调用工具，或 finish。只输出一个 JSON 动作。"
    )


def correction_message(reason: str, state: dict[str, Any]) -> str:
    return (
        f"上一步动作未被接受：{reason}\n\n"
        f"当前状态：\n{json.dumps(state_summary(state), ensure_ascii=False, indent=2)}\n\n"
        "请修正后只输出一个 JSON 动作。"
    )
