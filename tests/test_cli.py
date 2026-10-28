from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, input_dir: str, scenario: str, extra: list[str] | None = None):
    out = tmp_path / "out.json"
    trace = tmp_path / "trace.json"
    store = tmp_path / "drafts.json"
    cmd = [
        sys.executable,
        "-m",
        "src.main",
        "--input",
        str(ROOT / "examples" / input_dir / "input.json"),
        "--provider",
        "mock",
        "--scenario",
        scenario,
        "--store",
        str(store),
        "--trace",
        str(trace),
        "--out",
        str(out),
    ]
    if extra:
        cmd.extend(extra)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return proc, payload, trace, store


def test_cli_normal(tmp_path):
    proc, payload, trace, _ = run_cli(tmp_path, "01_normal", "normal")
    assert proc.returncode == 0, proc.stderr
    assert payload["status"] == "completed"
    assert payload["draft"]["ok"] is True
    assert trace.exists()
    assert trace.with_suffix(".md").exists()


def test_cli_save_failure_exit_code(tmp_path):
    proc, payload, _, _ = run_cli(tmp_path, "06_save_failure", "save_failure", ["--fail-save"])
    assert proc.returncode == 3
    assert payload["status"] == "save_failed"
    assert payload["draft"]["ok"] is False


def test_cli_max_steps_exit_code(tmp_path):
    proc, payload, _, _ = run_cli(tmp_path, "01_normal", "normal", ["--max-steps", "1"])
    assert proc.returncode == 3
    assert payload["status"] == "max_steps"
    assert "未完成" in payload["message"]


def test_cli_batch_mock(tmp_path):
    out = tmp_path / "batch_out.json"
    trace = tmp_path / "batch_trace.json"
    store = tmp_path / "drafts.json"
    cmd = [
        sys.executable,
        "-m",
        "src.main",
        "--input",
        str(ROOT / "examples" / "08_batch_normal" / "input.json"),
        "--provider",
        "mock",
        "--store",
        str(store),
        "--trace",
        str(trace),
        "--out",
        str(out),
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stderr
    assert payload["status"] == "pending_confirmation"
    assert payload["total"] == 2
    assert payload["results"][1]["suggested_slot"]["start"] == "10:30"
    assert trace.exists()
    assert (tmp_path / "traces").exists()
    assert len(list((tmp_path / "traces").glob("*.json"))) == 2


def test_cli_duplicate_second_run(tmp_path):
    first_proc, first, _, store = run_cli(tmp_path, "05_duplicate", "duplicate")
    assert first_proc.returncode == 0
    assert first["draft"]["duplicate"] is False

    second_proc, second, _, _ = run_cli(tmp_path, "05_duplicate", "duplicate")
    assert second_proc.returncode == 0
    assert second["draft"]["duplicate"] is True
    assert second["draft"]["draft_id"] == first["draft"]["draft_id"]
    drafts = json.loads(store.read_text(encoding="utf-8"))["drafts"]
    assert len(drafts) == 1
