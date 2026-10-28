from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import RunResult, StepRecord, utc_now


def _short(value: Any, limit: int = 320) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(trace: dict[str, Any]) -> str:
    lines = [
        f"# Trace: {trace.get('run_id', '')}",
        "",
        f"- Provider: `{trace.get('provider')}`",
        f"- Model: `{trace.get('model')}`",
        f"- Status: `{trace.get('final', {}).get('status')}`",
        f"- Max steps: {trace.get('max_steps')}",
        f"- Input: `{_short(trace.get('input'), 500)}`",
        "",
        "| # | Action | Args (summary) | Observation (summary) | Decision basis |",
        "|---|---|---|---|---|",
    ]
    for step in trace.get("steps", []):
        lines.append(
            "| {step} | `{action}` | {args} | {obs} | {basis} |".format(
                step=step.get("step"),
                action=step.get("action"),
                args=_short(step.get("args", {})),
                obs=_short(step.get("observation", {})),
                basis=(step.get("decision_basis") or "").replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "## Final",
            "",
            "```json",
            json.dumps(trace.get("final", {}), ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def build_trace(
    run_id: str,
    input_data: dict[str, Any],
    provider: str,
    model: str,
    max_steps: int,
    steps: list[StepRecord],
    result: RunResult,
    started_at: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": utc_now(),
        "provider": provider,
        "model": model,
        "max_steps": max_steps,
        "input": input_data,
        "steps": [step.to_dict() for step in steps],
        "final": result.to_dict(),
    }


def render_batch_markdown(trace: dict[str, Any]) -> str:
    lines = [
        f"# Batch Trace: {trace.get('batch_id', '')}",
        "",
        f"- Status: `{trace.get('status')}`",
        f"- Total: {trace.get('total')}",
        f"- Counts: `{json.dumps(trace.get('counts', {}), ensure_ascii=False)}`",
        "",
        "| # | Material | Status | Steps | Draft | Message |",
        "|---|---|---|---|---|---|",
    ]
    for item in trace.get("items", []):
        result = item.get("result") or {}
        draft = result.get("draft") or {}
        draft_text = draft.get("draft_id") or draft.get("error") or "none"
        lines.append(
            "| {index} | `{material}` | `{status}` | {steps} | {draft} | {message} |".format(
                index=item.get("index"),
                material=result.get("material_id"),
                status=result.get("status"),
                steps=result.get("steps"),
                draft=draft_text,
                message=(result.get("message") or "").replace("|", "\\|"),
            )
        )
    lines.extend(
        [
            "",
            "## Items",
            "",
            "```json",
            json.dumps(trace.get("items", []), ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_batch_trace(trace: dict[str, Any], path: str | Path) -> tuple[Path, Path]:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_batch_markdown(trace), encoding="utf-8")
    return json_path, md_path


def write_trace(trace: dict[str, Any], path: str | Path) -> tuple[Path, Path]:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(trace, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path = json_path.with_suffix(".md")
    md_path.write_text(render_markdown(trace), encoding="utf-8")
    return json_path, md_path
