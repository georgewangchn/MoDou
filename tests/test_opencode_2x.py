"""Regression tests for opencode 2.x CLI adaptation.

Covers the three `--format json` parsing points and the 2.x command shape:
- `run --dir` removed  → directory conveyed via subprocess cwd
- `--dangerously-skip-permissions` removed (hangs) → `--auto` or per-repo
  opencode.json permission whitelist
- `--session <id> --continue` → `--session <id>` alone

The recorded fixture (tests/fixtures/opencode2x_run_events.jsonl) is a real
`opencode run --format json` capture from opencode v2.x.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from asr.agents.opencode_backend import (
    _build_opencode_cmd,
    _check_error_events,
    _parse_session_id,
    _parse_text,
    _parse_tokens,
)
from asr.cli.main import EXIT_CONVERGED, EXIT_ENV_ERROR, EXIT_NOT_CONVERGED
from asr.controller.convergence import ConvergenceResult, ConvergenceState
from asr.spec.models import Specification

FIXTURE = Path(__file__).parent / "fixtures" / "opencode2x_run_events.jsonl"


def _load_fixture() -> str:
    return FIXTURE.read_text()


# ---------------------------------------------------------------------------
# Command construction (opencode 2.x flags)
# ---------------------------------------------------------------------------

def test_cmd_has_no_removed_flags():
    cmd = _build_opencode_cmd(Path("/tmp/proj"))
    assert "--dir" not in cmd
    assert "--dangerously-skip-permissions" not in cmd
    assert "--continue" not in cmd


def test_cmd_uses_auto_by_default():
    cmd = _build_opencode_cmd(Path("/tmp/proj"))
    assert "--auto" in cmd


def test_cmd_auto_disabled_for_permission_whitelist(monkeypatch):
    # Per-repo opencode.json permission whitelist mode: no --auto flag.
    monkeypatch.setenv("ASR_OPENCODE_AUTO", "0")
    cmd = _build_opencode_cmd(Path("/tmp/proj"))
    assert "--auto" not in cmd


def test_cmd_session_continuation_without_continue_flag():
    # opencode 2.x: `--session <id>` alone resumes/creates the session.
    cmd = _build_opencode_cmd(Path("/tmp/proj"), session_id="ses_abc")
    assert "--session" in cmd
    i = cmd.index("--session")
    assert cmd[i + 1] == "ses_abc"
    assert "--continue" not in cmd


def test_cmd_keeps_json_format():
    cmd = _build_opencode_cmd(Path("/tmp/proj"))
    i = cmd.index("--format")
    assert cmd[i + 1] == "json"


# ---------------------------------------------------------------------------
# Parse point 1: sessionID
# ---------------------------------------------------------------------------

def test_parse_session_id_from_fixture():
    stdout = _load_fixture()
    sid = _parse_session_id(stdout)
    assert sid is not None
    assert sid.startswith("ses_")


def test_parse_session_id_missing():
    assert _parse_session_id("") is None
    assert _parse_session_id("not json\n{\"type\":\"step_start\"}\n") is None


# ---------------------------------------------------------------------------
# Parse point 2: step_finish.tokens
# ---------------------------------------------------------------------------

def test_parse_tokens_from_fixture():
    prompt, completion, total = _parse_tokens(_load_fixture())
    assert prompt > 0
    assert completion > 0
    assert total >= prompt + completion  # total may include reasoning/cache
    assert (prompt, completion, total) == _parse_tokens(_load_fixture())


def test_parse_tokens_accumulates_multiple_steps():
    def finish(inp: int, out: int) -> str:
        return json.dumps({
            "type": "step_finish",
            "part": {"type": "step-finish", "tokens": {
                "input": inp, "output": out, "total": inp + out}},
        })

    stdout = f"{finish(100, 50)}\n{finish(200, 25)}\n"
    prompt, completion, total = _parse_tokens(stdout)
    assert (prompt, completion, total) == (300, 75, 375)


def test_parse_tokens_ignores_other_events():
    stdout = json.dumps({
        "type": "step_start",
        "part": {"type": "step-start", "snapshot": "abc", "tokens": {
            "input": 999, "output": 999, "total": 999}},
    }) + "\n"
    assert _parse_tokens(stdout) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Parse point 3: text parts
# ---------------------------------------------------------------------------

def test_parse_text_from_fixture():
    # Real 2.x shape: single text part carries the full message (no deltas).
    assert _parse_text(_load_fixture()) == "OK"


def test_parse_text_extracts_2x_text_parts():
    events = [
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "text", "part": {"type": "text", "text": "Hello "}},
        {"type": "text", "part": {"type": "text", "text": "world"}},
        {"type": "step_finish", "part": {"type": "step-finish", "tokens": {}}},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    assert _parse_text(stdout) == "Hello world"


def test_parse_text_part_type_only_marker():
    # 2.x may mark text via part.type even when the top-level type differs.
    events = [
        {"type": "message.updated", "part": {"type": "text", "text": "abc"}},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    assert _parse_text(stdout) == "abc"


def test_parse_text_ignores_tool_parts():
    events = [
        {"type": "tool", "part": {"type": "tool", "text": "leak"}},
        {"type": "text", "part": {"type": "text", "text": "real"}},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\n"
    assert _parse_text(stdout) == "real"


def test_parse_text_legacy_message_content():
    stdout = json.dumps({"message": {"content": "legacy"}}) + "\n"
    assert _parse_text(stdout) == "legacy"


def test_parse_text_skips_non_json_lines():
    stdout = "noise\n" + json.dumps({"part": {"type": "text", "text": "ok"}}) + "\n\n"
    assert _parse_text(stdout) == "ok"


# ---------------------------------------------------------------------------
# Error payloads (opencode 2.x emits these on stdout, often with exit 0)
# ---------------------------------------------------------------------------

def test_error_payload_raises():
    stdout = json.dumps({
        "error": {"type": "unknown",
                  "message": "Working directory does not exist: /tmp/x"},
        "content": [],
    }) + "\n"
    with pytest.raises(RuntimeError, match="Working directory does not exist"):
        _check_error_events(stdout)


def test_error_event_type_raises():
    stdout = json.dumps({"type": "error", "message": "boom"}) + "\n"
    with pytest.raises(RuntimeError, match="boom"):
        _check_error_events(stdout)


def test_no_error_payload_passes():
    _check_error_events(_load_fixture())  # healthy stream, must not raise


# ---------------------------------------------------------------------------
# Minimal spec projection (ousheng work-item description → spec YAML)
# ---------------------------------------------------------------------------

def test_spec_acceptance_criteria_alias():
    spec = Specification(
        goal="Fix login bug",
        acceptance_criteria=["login returns 200", "session cookie set"],
    )
    assert len(spec.acceptance) == 2
    assert spec.acceptance[0].name == "login returns 200"
    assert spec.acceptance[1].name == "session cookie set"
    assert spec.validate_spec().valid


def test_spec_acceptance_dict_entries_untouched():
    spec = Specification(
        goal="G",
        acceptance=[{"name": "a1", "description": "d", "expected_behavior": "b"}],
    )
    assert spec.acceptance[0].expected_behavior == "b"


def test_spec_acceptance_mixed_string_and_dict():
    spec = Specification(
        goal="G",
        acceptance=["plain string", {"name": "dict-entry"}],
    )
    assert spec.acceptance[0].name == "plain string"
    assert spec.acceptance[1].name == "dict-entry"


def test_spec_minimal_goal_only():
    spec = Specification(goal="Build feature X")
    assert spec.acceptance == []
    assert spec.validate_spec().valid


# ---------------------------------------------------------------------------
# CLI exit-code contract (ousheng subprocess integration)
#   0 = converged, 1 = not converged, 2 = environment error
# ---------------------------------------------------------------------------

def _make_runtime_stub(state: ConvergenceState):
    stub_result = ConvergenceResult(state=state, iterations=2, events=[], summary={})

    async def _run(*args, **kwargs):
        return stub_result

    return _run


def _invoke_run(argv: list[str], state: ConvergenceState):
    from click.testing import CliRunner
    from asr.cli.main import cli
    runner = CliRunner()
    with patch("asr.cli.main.ASRRuntime") as runtime_cls, \
         patch("shutil.which", return_value="/usr/local/bin/opencode"):
        runtime_cls.return_value.run = _make_runtime_stub(state)
        return runner.invoke(cli, ["run", *argv])


def test_run_exit_code_converged(tmp_path):
    result = _invoke_run(["--project", str(tmp_path)], ConvergenceState.CONVERGED)
    assert result.exit_code == EXIT_CONVERGED


def test_run_exit_code_not_converged(tmp_path):
    result = _invoke_run(["--project", str(tmp_path)], ConvergenceState.STUCK)
    assert result.exit_code == EXIT_NOT_CONVERGED


def test_run_exit_code_env_error_when_opencode_missing(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from asr.cli.main import cli
    monkeypatch.setenv("PATH", str(tmp_path))  # no opencode anywhere
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--project", str(tmp_path)])
    assert result.exit_code == EXIT_ENV_ERROR
