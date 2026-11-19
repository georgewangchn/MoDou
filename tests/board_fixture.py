"""Shared fixture: a synthetic OuSheng board git repo for tests.

Builds an in-memory board matching the real tide-datax structure:
`.ousheng/{project,systems,roles}.yaml`, `work/*.yaml` (schema_version=2),
`designs/<topic>/design.md` with frontmatter, and a `docs/` evidence tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

WORK_TMPL = """schema_version: 2
id: {id}
type: {type}
title: {title}
system: {system}
status: {status}
revision: {rev}
priority: {priority}
assignee: {assignee}
acting_role: {acting_role}
accountable_human: pm
{extra}"""

DESIGN_TMPL = """---
status: {status}
owner: {owner}
systems:
{systems}
related_items:
{related}
decided_by: {decided_by}
decided_at: "2026-09-23T10:07:20+08:00"
---

# {title}

{body}"""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_TERMINAL_PROMPT": "0",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")
    return proc


def build_board(root: Path) -> Path:
    """Create a synthetic board repo at <root>/board and return its path."""
    board = root / "board"
    (board / ".ousheng/work").mkdir(parents=True)
    (board / ".ousheng/designs/layered").mkdir(parents=True)
    (board / "docs").mkdir(parents=True)

    (board / ".ousheng/project.yaml").write_text("id: synth-board\nname: Synth Board\n")
    (board / ".ousheng/systems.yaml").write_text(
        "systems:\n  - id: data\n    name: data\n  - id: mlake\n    name: mlake\n"
        "  - id: ui\n    name: ui\n")
    (board / ".ousheng/roles.yaml").write_text("roles:\n  - id: dev\n  - id: test\n")

    def work(iid, type_, title, system, status, extra="", rev=1, priority="P1",
             assignee="data", acting_role="dev"):
        (board / f".ousheng/work/{iid}.yaml").write_text(WORK_TMPL.format(
            id=iid, type=type_, title=title, system=system, status=status,
            rev=rev, priority=priority, assignee=assignee, acting_role=acting_role,
            extra=extra))

    # T0: design closure items (three systems + integration test)
    work("T-101", "feature", "分层开发：data 域周期实例与调度器（design:layered）", "data", "done",
         extra="description: 实现 Flink 作业周期实例与调度器\n")
    work("T-102", "feature", "分层开发：mlake 域资源面（design:layered）", "mlake", "done",
         extra="contract:\n  kind: http\n  status: agreed\n  breaking: false\n")
    work("T-103", "feature", "分层开发：ui 域页面（design:layered）", "ui", "done")
    work("T-104", "test", "分层开发：三系统端到端联合测试", "data", "testing",
         assignee="test", acting_role="test")
    # T0: breaking contract + release axis
    work("T-105", "task", "API 契约升级（破坏性）", "data", "done",
         extra="target_version: v2.1\ncontract:\n  kind: http\n  status: live\n  breaking: true\n"
               "evidence:\n  - type: git_commit\n    source: git\n    locator: deadbeef\n    result: passed\n")
    work("T-106", "release", "v2.1 发布", "data", "done",
         extra="target_version: v2.1\n")
    # T1: ordinary done bug without evidence
    work("BUG-201", "bug", "导出空指针修复", "ui", "done", assignee="ui")
    # T2: notice / cancelled / backlog
    work("N-301", "notice", "公告：例行维护", "data", "done")
    work("BUG-302", "bug", "误报问题", "ui", "cancelled")
    work("T-303", "task", "未开始任务", "data", "backlog")

    systems_yaml = "\n".join(f"    - {s}" for s in ("data", "mlake", "ui"))
    related_yaml = "\n".join(f"    - {i}" for i in ("T-101", "T-102", "T-103", "T-104"))
    (board / ".ousheng/designs/layered/design.md").write_text(DESIGN_TMPL.format(
        status="agreed", owner="data", systems=systems_yaml, related=related_yaml,
        decided_by="pm", title="分层开发 P0",
        body=("## 义务\n1. data 域实现周期实例与调度器\n"
              "2. mlake 域提供资源面并保持 http 契约兼容\n"
              "3. ui 域提供实例列表/详情页面\n"
              "4. 三系统通过端到端联合测试\n")))

    # Evidence tree referenced by locators
    (board / "docs/acceptance").mkdir(parents=True, exist_ok=True)
    (board / "docs/acceptance/T-101.md").write_text(
        "# T-101 验收\n- pytest 12 passed\n- 调度器周期触发正常\n")
    # Attach document evidence to T-101
    t101 = board / ".ousheng/work/T-101.yaml"
    t101.write_text(t101.read_text() +
                    "evidence:\n  - type: document\n    source: modou-test\n"
                    "    locator: docs/acceptance/T-101.md\n    result: passed\n")

    _git(board, "init", "-q")
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "synth board")
    return board
