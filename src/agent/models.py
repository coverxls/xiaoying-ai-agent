from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    COMPLETED = "completed"
    PENDING_CONFIRMATION = "pending_confirmation"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    SAVE_FAILED = "save_failed"
    MAX_STEPS = "max_steps"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        if self.error:
            out["error"] = self.error
        if self.message:
            out["message"] = self.message
        out.update(self.data)
        return out


@dataclass
class AgentAction:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    decision_basis: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AgentAction":
        if not isinstance(raw, dict):
            raise ValueError("action must be a JSON object")
        action = raw.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action must be a non-empty string")
        args = raw.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("args must be a JSON object")
        basis = raw.get("decision_basis", "")
        if not isinstance(basis, str):
            basis = str(basis)
        return cls(action=action.strip(), args=args, decision_basis=basis.strip())


@dataclass
class StepRecord:
    step: int
    action: str
    args: dict[str, Any]
    observation: dict[str, Any]
    decision_basis: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action,
            "args": self.args,
            "observation": self.observation,
            "decision_basis": self.decision_basis,
            "timestamp": self.timestamp,
        }


@dataclass
class RunResult:
    status: str
    material_id: str
    message: str
    audit: dict[str, Any] = field(default_factory=dict)
    suggested_slot: dict[str, Any] | None = None
    pending_items: list[str] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    steps: int = 0
    trace_path: str | None = None
    run_id: str = ""
    published: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "material_id": self.material_id,
            "audit": self.audit,
            "suggested_slot": self.suggested_slot,
            "pending_items": self.pending_items,
            "draft": self.draft,
            "steps": self.steps,
            "trace_path": self.trace_path,
            "message": self.message,
            "published": False,
        }


def json_safe(value: Any) -> Any:
    """Convert values that json.dumps cannot handle into strings."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
