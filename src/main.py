from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent.batch import BatchRunner, normalize_batch_input
from .agent.llm import build_llm
from .agent.loop import AgentLoop
from .agent.mock_scripts import PolicyMockLLM
from .agent.store import JsonDraftStore
from .agent.tools import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="素材审核与发布排期 Agent（只保存草稿，不发布）"
    )
    parser.add_argument("--input", required=True, help="输入 JSON 文件路径；支持单素材或批量数组")
    parser.add_argument(
        "--provider",
        default=os.getenv("LLM_PROVIDER", "mock"),
        choices=["mock", "deepseek", "openai"],
        help="mock 只验证流程；deepseek/openai 使用真实模型",
    )
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "deepseek-chat"))
    parser.add_argument(
        "--base-url",
        default=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
    )
    parser.add_argument("--api-key", default=None, help="优先生效；也可用 LLM_API_KEY 环境变量")
    parser.add_argument("--max-steps", type=int, default=8, help="每个素材的最大步数")
    parser.add_argument("--store", default="runtime/drafts.json", help="草稿 JSON 存储路径")
    parser.add_argument("--trace", default=None, help="Trace JSON 输出路径；批量时作为 batch trace")
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径；默认打印到 stdout")
    parser.add_argument("--scenario", default=None, help="单素材 mock 场景：normal/missing/risk/conflict/duplicate/save_failure")
    parser.add_argument("--fail-save", action="store_true", help="注入草稿保存失败")
    parser.add_argument("--reset-store", action="store_true", help="运行前清空草稿库")
    parser.add_argument("--quiet", action="store_true", help="不打印人类可读摘要")
    return parser


def _is_batch(raw: object) -> bool:
    return isinstance(raw, list) or (
        isinstance(raw, dict) and isinstance(raw.get("materials"), list)
    )


def _write_payload(payload: dict, out: str | None) -> None:
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _run_single(user_input: dict, args: argparse.Namespace) -> int:
    store = JsonDraftStore(args.store, fail_save=args.fail_save)
    if args.reset_store:
        store.reset()
    tools = ToolRegistry(store=store)

    scenario = args.scenario or user_input.get("scenario") or "normal"
    try:
        llm = build_llm(
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            scenario=scenario,
        )
    except Exception as exc:
        print(f"failed to build model client: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    loop = AgentLoop(llm=llm, tools=tools, max_steps=args.max_steps)
    result = loop.run(user_input, trace_path=args.trace)
    payload = result.to_dict()
    _write_payload(payload, args.out)

    if not args.quiet:
        draft = payload.get("draft") or {}
        print(
            "[{status}] material={material} draft={draft} trace={trace}".format(
                status=payload["status"],
                material=payload["material_id"],
                draft=draft.get("draft_id") or draft.get("error") or "none",
                trace=payload.get("trace_path"),
            ),
            file=sys.stderr,
        )
        print(payload.get("message", ""), file=sys.stderr)

    if payload["status"] in {"save_failed", "max_steps", "invalid_model_output"}:
        return 3
    return 0


def _run_batch(raw: object, args: argparse.Namespace) -> int:
    try:
        items = normalize_batch_input(raw)
    except ValueError as exc:
        print(f"invalid batch input: {exc}", file=sys.stderr)
        return 1

    store = JsonDraftStore(args.store, fail_save=args.fail_save)
    if args.reset_store:
        store.reset()
    tools = ToolRegistry(store=store)

    if args.provider == "mock":
        def llm_factory(item: dict):
            return PolicyMockLLM()
    else:
        try:
            shared_llm = build_llm(
                provider=args.provider,
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
                scenario="auto",
            )
        except Exception as exc:
            print(f"failed to build model client: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        def llm_factory(item: dict):
            return shared_llm

    trace_path = Path(args.trace) if args.trace else None
    trace_dir = (trace_path.parent / "traces") if trace_path else Path("runtime/traces")
    runner = BatchRunner(
        llm_factory=llm_factory,
        tools=tools,
        max_steps=args.max_steps,
        trace_dir=trace_dir,
    )
    payload = runner.run(items, batch_trace_path=trace_path)
    _write_payload(payload, args.out)

    if not args.quiet:
        for item in payload["results"]:
            draft = item.get("draft") or {}
            print(
                "[{status}] {material} draft={draft} steps={steps}".format(
                    status=item["status"],
                    material=item["material_id"],
                    draft=draft.get("draft_id") or draft.get("error") or "none",
                    steps=item["steps"],
                ),
                file=sys.stderr,
            )
        print(payload.get("message", ""), file=sys.stderr)

    if payload["status"] in {"partial", "failed", "save_failed", "max_steps", "invalid_model_output"}:
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv()
    args = build_parser().parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"input not found: {input_path}", file=sys.stderr)
        return 1

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if _is_batch(raw):
        return _run_batch(raw, args)
    if not isinstance(raw, dict):
        print("input must be a JSON object or a batch array", file=sys.stderr)
        return 1
    return _run_single(raw, args)


if __name__ == "__main__":
    raise SystemExit(main())
