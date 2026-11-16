"""
OpenCode CLI backend — all ASR agents delegate to `opencode run`.

opencode 2.x compatibility notes:
- `--dir <path>` was removed; the working directory is selected by running
  the subprocess with `cwd=project_dir` (the root command's positional
  `<directory>` argument is not accepted by the `run` subcommand).
- `--dangerously-skip-permissions` no longer exists (it makes the CLI hang
  waiting for an interactive prompt that never comes). Permissions are
  granted via `--auto` (auto-approve anything not explicitly denied) or,
  alternatively, a per-repo `opencode.json` permission whitelist — set
  ``ASR_OPENCODE_AUTO=0`` to drop `--auto` and rely solely on the whitelist.
- `--session <id>` alone resumes/creates a session; combining it with
  `--continue` is no longer needed and 2.x `--continue` means "resume the
  *last* session", which would fight the explicit `--session` id.

`--format json` event stream parsing (three extraction points):
1. ``sessionID`` — top-level camelCase key on every event.
2. ``step_finish`` events — ``part.tokens.{total,input,output}`` accumulated.
3. text parts — events carrying a text part (``type == "text"`` or
   ``part.type == "text"``), concatenated in stream order; legacy
   ``message.content`` events still honoured.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

OPENCODE_TIMEOUT = int(os.environ.get("ASR_OPENCODE_TIMEOUT", "24400"))
VERBOSE = os.environ.get("ASR_VERBOSE", "") != ""
# asyncio create_subprocess_exec default stream buffer is 64KB.
# opencode --format json emits long lines (tool results with embedded file contents).
# 10MB default prevents "Separator is found, but chunk is longer than limit" errors.
STREAM_LIMIT = int(os.environ.get("ASR_STREAM_LIMIT", str(10 * 1024 * 1024)))


def _print_progress(data: dict, label: str) -> None:
    if not VERBOSE:
        return
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    msg_type = data.get("type", "")
    part = data.get("part", {})
    if msg_type == "step_start" or part.get("type") == "step-start":
        # opencode 2.x: step-start parts carry no tool name; the following
        # tool part does. Nothing to show here.
        return
    if msg_type == "tool" or part.get("type") == "tool":
        name = part.get("tool", "") or part.get("name", "")
        title = part.get("title", "") or part.get("call", "")
        if name:
            preview = str(title).strip().replace("\n", " ")[:120]
            suffix = f" {preview}" if preview else ""
            print(f"  {ts} [{label}] {name}{suffix}", file=sys.stderr, flush=True)
        return
    if msg_type == "text" or part.get("type") == "text":
        text = part.get("text", "") or data.get("content", "")
        if text:
            preview = text.strip().replace("\n", " ")[:200]
            print(f"  {ts} [{label}] {preview}", file=sys.stderr, flush=True)


def _parse_session_id(stdout: str) -> str | None:
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            sid = data.get("sessionID")
            if sid:
                return sid
        except json.JSONDecodeError:
            pass
    return None


def _parse_tokens(stdout: str) -> tuple[int, int, int]:
    total = prompt = completion = 0
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "step_finish":
            t = data.get("part", {}).get("tokens", {})
            total += t.get("total", 0)
            prompt += t.get("input", 0)
            completion += t.get("output", 0)
    return prompt, completion, total


def _parse_text(stdout: str) -> str:
    parts = []
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        # opencode 2.x marks text parts with `part.type: "text"` (dashed)
        # while the top-level `type` keeps the snake_case event name.
        part = data.get("part", {})
        if isinstance(part, dict):
            if data.get("type") == "text" or part.get("type") == "text":
                text = part.get("text", "") or part.get("content", "")
                if text:
                    parts.append(text)
                continue
        if isinstance(data.get("message"), dict):
            text = data["message"].get("content", "")
            if text:
                parts.append(text)
    return "".join(parts)


def _check_error_events(stdout_text: str) -> None:
    """Raise on opencode 2.x error payloads.

    opencode 2.x reports invocation errors (bad working directory, auth,
    provider config...) as {"error": {...}} on stdout, often with exit
    code 0. Surface them as exceptions so the CLI classifies the run as
    an environment error (exit code 2) instead of a silent non-convergence.
    """
    for line in stdout_text.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        err = data.get("error")
        if isinstance(err, dict):
            raise RuntimeError(
                f"opencode error: {err.get('message') or err} (type={err.get('type', 'unknown')})")
        if data.get("type") == "error":
            raise RuntimeError(f"opencode error event: {data}")


def _build_opencode_cmd(project_dir: Path, session_id: str | None = None,
                        temperature: float | None = None) -> list[str]:
    # opencode 2.x: `run` no longer accepts `--dir`; the project directory is
    # conveyed via the subprocess cwd (see _run_opencode).
    cmd = [
        "opencode", "run",
        "--format", "json",
    ]
    # opencode 2.x: --dangerously-skip-permissions is gone (hangs waiting for
    # an interactive prompt). `--auto` auto-approves permissions that are not
    # explicitly denied. Set ASR_OPENCODE_AUTO=0 to instead rely on a per-repo
    # opencode.json permission whitelist.
    if os.environ.get("ASR_OPENCODE_AUTO", "1") != "0":
        cmd.append("--auto")
    if session_id:
        # opencode 2.x: --session <id> resumes (or creates) that exact
        # session on its own; do NOT pair it with --continue, whose 2.x
        # meaning is "resume the last session".
        cmd.extend(["--session", session_id])
    # NOTE: opencode does not support a --temperature flag.
    # Temperature control is handled via opencode's config or model variant.
    # The temperature parameter is accepted by this function for API compatibility
    # but silently ignored at the CLI level.
    # if temperature is not None:
    #     cmd.extend(["--temperature", str(temperature)])
    return cmd


async def _run_opencode(prompt: str, project_dir: Path,
                        session_id: str | None = None,
                        label: str = "",
                        temperature: float | None = None) -> tuple[str, str | None, int, int, int]:
    cmd = _build_opencode_cmd(project_dir, session_id, temperature)
    cmd.append(prompt)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        limit=STREAM_LIMIT,
        # opencode 2.x removed `run --dir`; the working directory is the
        # subprocess cwd.
        cwd=str(project_dir),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    assert proc.stdout is not None and proc.stderr is not None
    try:
        stdout_lines: list[str] = []
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode()
            stdout_lines.append(line)
            try:
                _print_progress(json.loads(line.strip()), label)
            except json.JSONDecodeError:
                pass
        stderr_bytes = await proc.stderr.read()
        await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise
    except Exception:
        # readline may raise ValueError if a single JSON line exceeds STREAM_LIMIT.
        # Ensure the subprocess is cleaned up before re-raising.
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise

    stdout_text = "".join(stdout_lines)
    stderr_text = stderr_bytes.decode() if stderr_bytes else ""

    if proc.returncode and proc.returncode != -15:
        print(f"[opencode] exit={proc.returncode} err={stderr_text[:200]}", file=sys.stderr)

    _check_error_events(stdout_text)

    new_session_id = _parse_session_id(stdout_text)
    pt, ct, tt = _parse_tokens(stdout_text)
    text = _parse_text(stdout_text)
    return text, new_session_id or session_id, pt, ct, tt


async def opencode_completion(prompt: str, project_dir: Path,
                              label: str = "") -> tuple[str, int, int, int]:
    text, _, pt, ct, tt = await _run_opencode(prompt, project_dir, label=label)
    return text, pt, ct, tt


async def opencode_run(prompt: str, project_dir: Path,
                       session_id: str | None = None,
                       label: str = "",
                       temperature: float | None = None) -> tuple[str | None, int, int, int]:
    _, new_sid, pt, ct, tt = await _run_opencode(prompt, project_dir, session_id, label=label, temperature=temperature)
    return new_sid, pt, ct, tt
