from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .loop import AgentLoop
from .models import RunResult, utc_now
from .tools import ToolRegistry
from .trace import write_batch_trace


MAX_BATCH_ITEMS = 20
SUCCESS_STATUSES = {"completed", "pending_confirmation", "skipped_duplicate"}
ALL_STATUSES = [
    "completed",
    "pending_confirmation",
    "needs_input",
    "blocked",
    "save_failed",
    "max_steps",
    "invalid_model_output",
    "skipped_duplicate",
]


def normalize_batch_input(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and isinstance(raw.get("materials"), list):
        items = raw["materials"]
    elif isinstance(raw, dict):
        items = [raw]
    else:
        raise ValueError("batch input must be a list, an object, or an object with a materials list")

    if not items:
        raise ValueError("batch input is empty")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"batch size {len(items)} exceeds limit {MAX_BATCH_ITEMS}")

    normalized = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"item {index} must be a JSON object")
        if not item.get("material_id"):
            raise ValueError(f"item {index} is missing material_id")
        normalized.append(dict(item))
    return normalized


def _counts(results: list[RunResult]) -> dict[str, int]:
    counts = {status: 0 for status in ALL_STATUSES}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _batch_status(results: list[RunResult]) -> str:
    statuses = [result.status for result in results]
    if not statuses:
        return "failed"
    # A skipped duplicate is not an independent failure: it inherits the
    # first occurrence's outcome, so ignore it when judging the batch.
    effective = [status for status in statuses if status != "skipped_duplicate"]
    if not effective:
        return "failed"
    if all(status == "completed" for status in effective):
        return "completed"
    if all(status in {"completed", "pending_confirmation"} for status in effective):
        return "pending_confirmation"
    if any(status in {"completed", "pending_confirmation"} for status in effective):
        return "partial"
    if len(set(effective)) == 1:
        return effective[0]
    return "failed"


class BatchRunner:
    def __init__(
        self,
        llm_factory: Callable[[dict[str, Any]], Any],
        tools: ToolRegistry,
        max_steps: int = 8,
        trace_dir: str | Path = "runtime/traces",
    ):
        self.llm_factory = llm_factory
        self.tools = tools
        self.max_steps = max_steps
        self.trace_dir = Path(trace_dir)

    def run(
        self,
        raw: Any,
        batch_trace_path: str | Path | None = None,
    ) -> dict[str, Any]:
        items = normalize_batch_input(raw)
        batch_id = "batch-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        started_at = utc_now()
        self.tools.reset_reservations()

        results: list[RunResult] = []
        seen: dict[str, RunResult] = {}
        for index, item in enumerate(items, 1):
            material_id = str(item["material_id"])
            if material_id in seen:
                first = seen[material_id]
                results.append(
                    RunResult(
                        status="skipped_duplicate",
                        material_id=material_id,
                        message="同一批次内重复素材，复用第 1 条结果；未重复创建草稿。",
                        audit=first.audit,
                        suggested_slot=first.suggested_slot,
                        pending_items=first.pending_items,
                        draft=first.draft,
                        steps=0,
                        trace_path=None,
                        run_id=f"{batch_id}-dup-{index:02d}",
                        published=False,
                    )
                )
                continue

            llm = self.llm_factory(item)
            loop = AgentLoop(
                llm=llm,
                tools=self.tools,
                max_steps=self.max_steps,
                trace_dir=self.trace_dir,
            )
            item_trace = self.trace_dir / f"{batch_id}_{index:02d}_{material_id}.json"
            result = loop.run(item, trace_path=item_trace)
            results.append(result)
            seen[material_id] = result

        counts = _counts(results)
        batch_status = _batch_status(results)
        message = (
            f"批量处理 {len(results)} 条："
            f"completed {counts['completed']}，"
            f"pending_confirmation {counts['pending_confirmation']}，"
            f"needs_input {counts['needs_input']}，"
            f"blocked {counts['blocked']}，"
            f"save_failed {counts['save_failed']}，"
            f"skipped_duplicate {counts['skipped_duplicate']}。"
        )

        trace = {
            "batch_id": batch_id,
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": batch_status,
            "total": len(results),
            "counts": counts,
            "items": [
                {
                    "index": index,
                    "input": item,
                    "result": result.to_dict(),
                    "trace_path": result.trace_path,
                }
                for index, (item, result) in enumerate(zip(items, results), 1)
            ],
        }
        path = Path(batch_trace_path) if batch_trace_path else self.trace_dir / f"{batch_id}.json"
        write_batch_trace(trace, path)

        return {
            "batch_id": batch_id,
            "status": batch_status,
            "total": len(results),
            "counts": counts,
            "results": [result.to_dict() for result in results],
            "trace_path": str(path),
            "message": message,
        }
