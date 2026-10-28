from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm import LLMClient, LLMError, parse_action
from .models import RunResult, StepRecord, ToolResult, utc_now
from .prompt import (
    correction_message,
    initial_messages,
    initial_state,
    observation_message,
    split_iso,
)
from .tools import ToolRegistry
from .trace import build_trace, write_trace


ALLOWED_FINISH = {"completed", "pending_confirmation", "needs_input", "blocked", "save_failed"}
FORBIDDEN_CLAIMS = ("已发布", "发布成功", "已经发布", "published successfully")


def _reject(message: str) -> ToolResult:
    return ToolResult(ok=False, error="finish_rejected", message=message)


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_steps: int = 8,
        trace_dir: str | Path = "runtime/traces",
    ):
        self.llm = llm
        self.tools = tools
        self.max_steps = max(1, int(max_steps))
        self.trace_dir = Path(trace_dir)

    def run(self, user_input: dict[str, Any], trace_path: str | Path | None = None) -> RunResult:
        run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        started_at = utc_now()
        state = initial_state(user_input)
        messages = initial_messages(user_input, state)
        steps: list[StepRecord] = []
        parse_errors = 0

        for step in range(1, self.max_steps + 1):
            try:
                raw = self.llm.complete(messages)
            except Exception as exc:
                result = self._result(
                    status="invalid_model_output",
                    message=f"模型调用失败：{type(exc).__name__}: {exc}",
                    state=state,
                    pending=["模型调用失败，任务未完成；未发布。"],
                    trace_path=trace_path,
                    run_id=run_id,
                    steps=steps,
                    user_input=user_input,
                    started_at=started_at,
                )
                return result

            try:
                action = parse_action(raw)
                parse_errors = 0
            except LLMError as exc:
                parse_errors += 1
                observation = {"ok": False, "error": "invalid_model_output", "message": str(exc)}
                steps.append(
                    StepRecord(
                        step=step,
                        action="<invalid_model_output>",
                        args={},
                        observation=observation,
                        decision_basis="模型没有输出合法 JSON 动作。",
                    )
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": correction_message(
                            "输出不是合法 JSON 动作，必须是包含 action/args/decision_basis 的单个 JSON 对象。",
                            state,
                        ),
                    }
                )
                if parse_errors >= 2:
                    return self._result(
                        status="invalid_model_output",
                        message="模型连续两次输出非法动作，任务未完成；未发布。",
                        state=state,
                        pending=["模型输出异常，任务未完成；未发布。"],
                        trace_path=trace_path,
                        run_id=run_id,
                        steps=steps,
                        user_input=user_input,
                        started_at=started_at,
                    )
                continue

            if action.action == "finish":
                verdict = self._validate_finish(action.args, state)
                steps.append(
                    StepRecord(
                        step=step,
                        action="finish",
                        args=action.args,
                        observation=verdict.to_dict(),
                        decision_basis=action.decision_basis,
                    )
                )
                if not verdict.ok:
                    messages.append({"role": "assistant", "content": raw})
                    messages.append(
                        {
                            "role": "user",
                            "content": correction_message(verdict.message or "finish 未被接受", state),
                        }
                    )
                    continue
                return self._result(
                    status=action.args["status"],
                    message=action.args.get("message", ""),
                    state=state,
                    pending=action.args.get("pending_items", []),
                    trace_path=trace_path,
                    run_id=run_id,
                    steps=steps,
                    user_input=user_input,
                    started_at=started_at,
                )

            result = self.tools.call(action.action, action.args)
            self._update_state(state, action.action, action.args, result)
            steps.append(
                StepRecord(
                    step=step,
                    action=action.action,
                    args=action.args,
                    observation=result.to_dict(),
                    decision_basis=action.decision_basis,
                )
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": observation_message(step, action.action, result.to_dict(), state),
                }
            )

        return self._result(
            status="max_steps",
            message=f"达到最大步数 {self.max_steps}，任务未完成；未发布。",
            state=state,
            pending=[f"达到最大步数 {self.max_steps}，请人工检查后重试。"],
            trace_path=trace_path,
            run_id=run_id,
            steps=steps,
            user_input=user_input,
            started_at=started_at,
        )

    def _update_state(
        self,
        state: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        if tool_name == "get_material":
            if result.ok:
                material = result.data.get("material", {})
                state["material_loaded"] = True
                state["material"] = material
                state["missing_fields"] = result.data.get("missing_fields", [])
                state["hard_requirement"] = material.get("hard_requirement")
                if not state.get("requested_publish_at"):
                    state["requested_publish_at"] = material.get("required_publish_at")
                if not state.get("requested_date") or not state.get("requested_time"):
                    date, time = split_iso(state.get("requested_publish_at"))
                    state["requested_date"] = state.get("requested_date") or date
                    state["requested_time"] = state.get("requested_time") or time
            else:
                state["material_loaded"] = False
                state["material_error"] = result.to_dict()
        elif tool_name == "check_rules":
            if result.ok:
                state["rules_checked"] = True
                state["blocked"] = bool(result.data.get("blocked"))
                state["risks"] = result.data.get("risks", [])
                state["warnings"] = result.data.get("warnings", [])
            else:
                state["rules_error"] = result.to_dict()
                if result.error == "missing_fields":
                    existing = list(state.get("missing_fields", []))
                    for field in result.data.get("missing_fields", []):
                        if field not in existing:
                            existing.append(field)
                    state["missing_fields"] = existing
        elif tool_name == "query_slots":
            if result.ok:
                state["slot_checked"] = True
                state["slot_result"] = result.data
                requested = result.data.get("requested", {})
                nearest = result.data.get("nearest_alternative")
                state["suggested_time"] = nearest or requested.get("start")
                state["suggested_slot"] = {
                    "date": result.data.get("date"),
                    "start": state["suggested_time"],
                    "end": None,
                    "platform": result.data.get("platform"),
                }
                if state["suggested_time"]:
                    for alt in result.data.get("alternatives", []):
                        if alt.get("start") == state["suggested_time"]:
                            state["suggested_slot"]["end"] = alt.get("end")
                            break
                    if not state["suggested_slot"]["end"]:
                        state["suggested_slot"]["end"] = requested.get("end")
            else:
                state["slot_error"] = result.to_dict()
        elif tool_name == "save_draft":
            state["draft_attempted"] = True
            if result.ok:
                state["draft"] = result.to_dict()
                state["draft_error"] = None
                state["duplicate"] = bool(result.data.get("duplicate"))
                # The saved draft is authoritative: if the model chose a
                # different alternative than the tool's first suggestion,
                # report the slot that was actually drafted.
                draft = result.data.get("draft") or {}
                if draft.get("slot"):
                    state["suggested_time"] = draft["slot"]
                    state["suggested_slot"] = {
                        "date": draft.get("date"),
                        "start": draft.get("slot"),
                        "end": None,
                        "platform": state.get("material", {}).get("platform"),
                    }
                    slot_result = state.get("slot_result") or {}
                    for alt in slot_result.get("alternatives", []):
                        if alt.get("start") == draft.get("slot"):
                            state["suggested_slot"]["end"] = alt.get("end")
                            break
                    if not state["suggested_slot"]["end"]:
                        requested = slot_result.get("requested") or {}
                        if draft.get("slot") == requested.get("start"):
                            state["suggested_slot"]["end"] = requested.get("end")
            else:
                state["draft"] = None
                state["draft_error"] = result.to_dict()

    def _validate_finish(self, args: dict[str, Any], state: dict[str, Any]) -> ToolResult:
        status = args.get("status")
        message = str(args.get("message", ""))
        pending = args.get("pending_items", [])
        if status not in ALLOWED_FINISH:
            return _reject(f"status 必须是 {sorted(ALLOWED_FINISH)}，收到 {status!r}")
        if not isinstance(pending, list):
            return _reject("pending_items 必须是数组")
        if any(claim in message for claim in FORBIDDEN_CLAIMS):
            return _reject("message 不得声称已发布；只能说明保存了草稿。")
        if state.get("material_error"):
            if status not in ("needs_input", "blocked"):
                return _reject("素材不存在，status 只能是 needs_input 或 blocked。")
            if not pending:
                return _reject("素材不存在时，pending_items 要说明需要用户提供什么。")
            return ToolResult(ok=True)
        if not state.get("material_loaded"):
            return _reject("尚未成功调用 get_material，不能结束。")

        missing = state.get("missing_fields", [])
        if missing:
            if status != "needs_input":
                return _reject(f"missing_fields={missing}，status 必须为 needs_input。")
            if not pending:
                return _reject("needs_input 必须提供 pending_items，逐项说明要补充什么。")
            return ToolResult(ok=True)
        if status == "needs_input":
            return _reject("没有 missing_fields，不能标记 needs_input。")

        if not state.get("rules_checked"):
            return _reject("尚未成功调用 check_rules，不能结束。")
        if state.get("blocked"):
            if status != "blocked":
                return _reject("规则存在 blocking 风险，status 必须为 blocked。")
            return ToolResult(ok=True)
        if status == "blocked":
            return _reject("规则没有 blocking 风险，不能标记 blocked。")

        if not state.get("slot_checked"):
            return _reject("尚未成功调用 query_slots，不能结束。")

        if status == "save_failed":
            if state.get("draft") and state["draft"].get("ok"):
                return _reject("草稿保存成功，不能标记 save_failed。")
            if not state.get("draft_error"):
                return _reject("没有保存失败记录，不能标记 save_failed。")
            return ToolResult(ok=True)

        if not state.get("draft_attempted"):
            return _reject("尚未调用 save_draft，不能结束。")
        if not state.get("draft") or not state["draft"].get("ok"):
            return _reject("草稿未保存成功，不能标记 completed/pending_confirmation。")

        requested = state.get("requested_time")
        suggested = state.get("suggested_time")
        changed = bool(requested and suggested and requested != suggested)
        draft_status = (state["draft"].get("draft") or {}).get("draft_status")
        if changed:
            if status != "pending_confirmation":
                return _reject(
                    f"建议时段 {suggested} 与原要求 {requested} 不同，status 必须为 pending_confirmation。"
                )
            if not pending:
                return _reject("改时段必须提供 pending_items，请用户确认是否接受替代时段。")
            if draft_status != "pending_confirmation":
                return _reject("改时段的草稿 draft_status 必须为 pending_confirmation。")
            return ToolResult(ok=True)

        if status == "completed":
            if draft_status != "ready":
                return _reject("原时段草稿的 draft_status 必须为 ready。")
            return ToolResult(ok=True)
        if status == "pending_confirmation":
            if not pending:
                return _reject("pending_confirmation 必须提供 pending_items。")
            if draft_status != "pending_confirmation":
                return _reject("pending_confirmation 草稿的 draft_status 必须为 pending_confirmation。")
            return ToolResult(ok=True)
        return _reject("finish 状态与当前状态不一致。")

    def _result(
        self,
        status: str,
        message: str,
        state: dict[str, Any],
        pending: list[str] | None,
        trace_path: str | Path | None,
        run_id: str,
        steps: list[StepRecord],
        user_input: dict[str, Any],
        started_at: str,
    ) -> RunResult:
        draft_result = state.get("draft")
        if draft_result and draft_result.get("ok"):
            draft = draft_result.get("draft") or {}
            draft_out: dict[str, Any] | None = {
                "ok": True,
                "draft_id": draft_result.get("draft_id"),
                "duplicate": bool(draft_result.get("duplicate")),
                "draft_status": draft.get("draft_status"),
                "saved_at": draft.get("created_at"),
                "published": False,
            }
        elif state.get("draft_error"):
            err = state["draft_error"]
            draft_out = {
                "ok": False,
                "error": err.get("error"),
                "message": err.get("message"),
                "published": False,
            }
        else:
            draft_out = None

        suggested = state.get("suggested_slot")
        if suggested:
            suggested = dict(suggested)
            requested = state.get("requested_time")
            suggested["requested_time"] = requested
            suggested["changed_from_request"] = bool(
                requested and suggested.get("start") and requested != suggested.get("start")
            )

        audit = {
            "status": "blocked" if state.get("blocked") else ("passed" if state.get("rules_checked") else "not_checked"),
            "risks": state.get("risks", []),
            "warnings": state.get("warnings", []),
            "missing_fields": state.get("missing_fields", []),
        }

        result = RunResult(
            status=status,
            material_id=str(state.get("material_id") or ""),
            message=message or "任务结束。",
            audit=audit,
            suggested_slot=suggested,
            pending_items=[str(item) for item in (pending or [])],
            draft=draft_out,
            steps=len(steps),
            trace_path=None,
            run_id=run_id,
            published=False,
        )

        path = Path(trace_path) if trace_path else self.trace_dir / f"{run_id}.json"
        result.trace_path = str(path)
        trace = build_trace(
            run_id=run_id,
            input_data=user_input,
            provider=getattr(self.llm, "name", "unknown"),
            model=getattr(self.llm, "last_response_model", None) or getattr(self.llm, "model", "unknown"),
            max_steps=self.max_steps,
            steps=steps,
            result=result,
            started_at=started_at,
        )
        write_trace(trace, path)
        return result
