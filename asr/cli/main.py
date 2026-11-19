from __future__ import annotations

import asyncio
from pathlib import Path

import click

from asr.config.loader import load_config, create_default_config
from asr.runtime import ASRRuntime
from asr.agents.llm_tracker import get_agent_tokens

# Exit codes for non-interactive callers (e.g. ousheng cruise mode driving
# MoDou as a subprocess). Keep stable: callers branch on them.
EXIT_CONVERGED = 0      # all acceptance criteria met, tests green
EXIT_NOT_CONVERGED = 1  # ran to max iterations / stuck, environment healthy
EXIT_ENV_ERROR = 2      # opencode CLI missing, bad spec/config, backend crash


@click.group()
def cli():
    pass


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory")
@click.option("--spec", default=None, type=click.Path(exists=True), help="Spec YAML file (optional; reads DESIGN.md if omitted)")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True), help="Config YAML file")
@click.option("--max-iterations", default=None, type=int, help="Override max iterations")
@click.option("--decoupled", is_flag=True, help="Use decoupled A2A mode (AgentRunner)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def run(project, spec, config_path, max_iterations, decoupled, verbose):
    """Run MoDou convergence loop (software engineering plugin; subprocess-safe).

    Minimal spec YAML (auto-projectable from a work-item description):

      goal: <what to build>
      acceptance_criteria:
        - <criterion 1>
        - <criterion 2>

    Exit codes: 0=converged, 1=not converged (max iterations/stuck),
    2=environment error (opencode CLI missing, bad spec/config, crash).
    """
    from asr.controller.convergence import ConvergenceState

    # Pre-flight: check opencode availability (environment error → exit 2)
    import shutil as _shutil
    if not _shutil.which("opencode"):
        click.echo("ERROR: 'opencode' CLI not found in PATH.")
        click.echo("Install it:  npm install -g opencode-ai")
        click.echo("Configure:   asr init --project .  (generates opencode.json template)")
        raise SystemExit(EXIT_ENV_ERROR)

    try:
        config = load_config(config_path) if config_path else create_default_config()
    except Exception as exc:
        click.echo(f"ERROR: failed to load config: {exc}")
        raise SystemExit(EXIT_ENV_ERROR)
    if max_iterations:
        config.convergence.max_iterations = max_iterations

    project_dir = Path(project).resolve()
    spec_path = Path(spec).resolve() if spec else None
    if spec_path is not None and not spec_path.exists():
        click.echo(f"ERROR: spec file not found: {spec_path}")
        raise SystemExit(EXIT_ENV_ERROR)
    runtime = ASRRuntime(config)

    mode = "解耦A2A" if decoupled else "直接模式"
    click.echo(f"MoDou 软件工程收敛插件 [{mode}]")
    click.echo(f"项目路径: {project_dir}")
    click.echo(f"规格文件: {spec_path or '(来自 DESIGN.md)'}")
    click.echo(f"最大迭代轮次: {config.convergence.max_iterations}")
    click.echo()

    async def _run():
        last = [0, 0, 0]

        def progress(iteration, errors, phase, failed, errored, detail=""):
            if iteration != last[0] or phase != last[2]:
                icon = "❌" if (failed or errored) else "✅" if errors == 0 else "🔧"
                label = {"TESTING": "测试验证", "ANALYZING": "规格分析", "BUILDING": "代码生成", "REPAIRING": "代码修复", "GENERATING": "初始生成"}.get(phase, phase)
                agent_key = {"TESTING": "tester", "ANALYZING": "analyzer", "BUILDING": "builder", "REPAIRING": "builder"}.get(phase, "")
                line = f"  [第{iteration}轮] {label}  错误:{errors}  {icon}"
                if agent_key:
                    t = get_agent_tokens(agent_key)
                    if t.get("calls", 0) > 0:
                        inp = _fmt_tokens(t["prompt_tokens"])
                        out = _fmt_tokens(t["completion_tokens"])
                        line += f"  | Token:{inp}/{out}"
                if detail:
                    line += f"  | {detail}"
                click.echo(line)
                last[0] = iteration
                last[1] = errors
                last[2] = phase

        return await runtime.run(project_dir, spec_path, use_decoupled=decoupled, progress_callback=progress)

    try:
        result = asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"ERROR: MoDou runtime crashed: {exc}")
        raise SystemExit(EXIT_ENV_ERROR)

    click.echo()
    dag_info = result.summary.get("dag", {}) if result.summary else {}
    if dag_info:
        click.echo(f"Mode: DAG | Nodes: {dag_info.get('total_nodes', '?')} | Converged: {dag_info.get('converged', '?')} | Stuck: {dag_info.get('stuck', '?')}")
        if result.state == ConvergenceState.CONVERGED:
            click.echo(f"✅ CONVERGED — all DAG nodes converged")
            exit_code = EXIT_CONVERGED
        else:
            click.echo(f"❌ STUCK — {dag_info.get('stuck', 0)} of {dag_info.get('total_nodes', '?')} nodes failed")
            exit_code = EXIT_NOT_CONVERGED
        click.echo(f"Iterations: {result.iterations}")
    else:
        _show_progress(result)
        click.echo()
        if result.state == ConvergenceState.CONVERGED:
            click.echo("✅ 已收敛 — 所有测试通过且规格一致")
            exit_code = EXIT_CONVERGED
        else:
            reason = result.events[-1].payload.get("reason", "unknown") if result.events else "unknown"
            click.echo(f"❌ 卡住 — {reason}")
            exit_code = EXIT_NOT_CONVERGED
        click.echo(f"迭代轮次: {result.iterations} | 事件数: {len(result.events)}")
        click.echo(f"\n📁 详细日志: .runtime/logs/asr.log")
        click.echo(f"📁 LLM 追踪: .runtime/logs/llm.jsonl")

    raise SystemExit(exit_code)


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory under assessment")
@click.option("--spec", default=None, type=click.Path(exists=True), help="Spec YAML file (optional; reads DESIGN.md if omitted)")
@click.option("--no-analyzer", is_flag=True, help="Skip semantic analysis; deterministic verification only")
@click.option("--json-output", default=None, type=click.Path(), help="Write structured Assessment to a JSON file")
@click.option("--timeout", default=600, type=int, help="pytest timeout in seconds")
@click.option("--basetemp", default=None, type=click.Path(), help="pytest basetemp (use a workspace path under sandboxes)")
def assess(project, spec, no_analyzer, json_output, timeout, basetemp):
    """独立评估：Tester 验证 + Analyzer 偏差分析。不调用 Builder，不修改交付物。

    Exit codes: 0=PASS, 1=DEVIATION/INCONCLUSIVE, 2=BLOCKED/environment error.
    """
    import json as _json

    from asr.assurance import STATUS_BLOCKED, STATUS_PASS, assess as run_assessment

    project_dir = Path(project).resolve()
    spec_path = Path(spec).resolve() if spec else None
    basetemp_path = Path(basetemp).resolve() if basetemp else None

    result = asyncio.run(run_assessment(
        project_dir,
        spec_path,
        use_analyzer=not no_analyzer,
        timeout=timeout,
        basetemp=basetemp_path,
    ))

    click.echo(result.to_text())
    if json_output:
        out = Path(json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        click.echo(f"\n📄 Assessment JSON: {out}")

    if result.status == STATUS_PASS:
        raise SystemExit(EXIT_CONVERGED)
    if result.status == STATUS_BLOCKED:
        raise SystemExit(EXIT_ENV_ERROR)
    raise SystemExit(EXIT_NOT_CONVERGED)


@cli.command(name="board-assess")
@click.option("--board", required=True, type=click.Path(exists=True),
              help="OuSheng board git clone (READ-ONLY; never pushed by this command)")
@click.option("--as-of", default="HEAD", help="Board git revision to pin the snapshot to")
@click.option("--design", "designs", multiple=True, help="Design topic(s) to assess (default: all decided)")
@click.option("--item", "item_ids", multiple=True, help="Explicit work item id(s) to assess individually")
@click.option("--sample-rate", default=0.0, type=float, help="T1 sampling rate 0..1 (ordinary done/testing items)")
@click.option("--rules-file", default=None, type=click.Path(exists=True),
              help="OuSheng published rules doc rendered into every dossier (rules page)")
@click.option("--rules-ref", default=None, help="Version ref for the rules doc (e.g. wth@<sha>)")
@click.option("--no-llm", is_flag=True,
              help="Plumbing mode: grade+assemble only; every assessment is INCONCLUSIVE by construction")
@click.option("--workdir", default=None, type=click.Path(), help="Staging dir for the assessor (default: temp)")
@click.option("--store-dir", default=None, type=click.Path(), help="Assessment archive dir (default: ./.modou/assessments)")
@click.option("--reassess-of", default=None, help="Assessment id this run re-verifies (only with --item)")
@click.option("--converge-report", default=None, type=click.Path(exists=True),
              help="OuSheng `converge` text output for the cross-check metric (CONFIRMED_OVERLAP/MODOU_ONLY/CONVERGE_ONLY)")
@click.option("--json-output", default=None, type=click.Path(), help="Write the full report JSON here")
def board_assess(board, as_of, designs, item_ids, sample_rate, rules_file,
                 rules_ref, no_llm, workdir, store_dir, reassess_of,
                 converge_report, json_output):
    """绳上保证（模式 C）：对 OuSheng board 只读影子偏差分析。

    两级分析：decided design → item 级 + design 级聚合；T1 按 --sample-rate 抽样。
    默认静默：结果只落 MODOU 自己的存档，不写 board。

    Exit codes: 0=all PASS, 1=any DEVIATION/INCONCLUSIVE, 2=BLOCKED/environment.
    """
    import json as _json

    from asr.adapters.ousheng.snapshot import SnapshotError
    from asr.assurance.pipeline import run_board_assessment
    from asr.assurance.store import AssessmentStore

    try:
        report = asyncio.run(run_board_assessment(
            board_dir=Path(board).resolve(),
            rev=as_of,
            designs=list(designs) or None,
            items=list(item_ids) or None,
            sample_rate=sample_rate,
            rules_file=rules_file,
            rules_ref=rules_ref,
            no_llm=no_llm,
            workdir=workdir,
            reassess_of=reassess_of,
            store=AssessmentStore(Path(store_dir) if store_dir else Path(".modou/assessments")),
        ))
    except (SnapshotError, ValueError) as e:
        click.echo(f"BLOCKED: {e}")
        raise SystemExit(EXIT_ENV_ERROR)

    c = report.tier_coverage
    click.echo(f"Board: {report.board}  as_of: {report.as_of_commit}  mode: {report.analyzer_mode}")
    click.echo(f"Tier coverage: T0={c.get('T0', 0)} T1={c.get('T1', 0)} "
               f"(sampled @ {report.sample_rate:.0%}) T2-skipped={c.get('T2', 0)}")
    click.echo("-" * 60)
    for a in report.assessments:
        click.echo(a.summary_line())
        for f in a.findings:
            click.echo(f"  · [{f.severity}/{f.confidence}] {f.code}: {f.actual[:100]}")
        for d in a.dropped_findings:
            click.echo(f"  ○ dropped: {d['code']} ({d['reason'][:80]})")
    if report.errors:
        click.echo("Errors: " + "; ".join(report.errors))

    report_dict = report.to_dict()
    if converge_report:
        from asr.assurance.xcheck import cross_check
        cc = cross_check(report_dict, Path(converge_report).read_text())
        report_dict["cross_check"] = cc.to_dict()
        click.echo("-" * 60)
        click.echo(f"Cross-check vs converge [{cc.converge_status}]: "
                   f"overlap={len(cc.confirmed_overlap)} "
                   f"modou_only={len(cc.modou_only)} "
                   f"converge_only={len(cc.converge_only)} "
                   f"disagreement=N/A(P2+)")

    if json_output:
        out = Path(json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(report_dict, ensure_ascii=False, indent=2))
        click.echo(f"\n📄 Report JSON: {out}")

    raise SystemExit(report.exit_code())


@cli.command(name="run-dag")
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory")
@click.option("--spec", required=True, type=click.Path(exists=True), help="Spec YAML file")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True), help="Config YAML file")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def run_dag(project, spec, config_path, verbose):
    config = load_config(config_path) if config_path else create_default_config()
    project_dir = Path(project).resolve()
    runtime = ASRRuntime(config)

    click.echo(f"MoDou Runtime [DAG Mode]")
    click.echo(f"Project: {project_dir}")
    click.echo()

    async def _run():
        return await runtime.run_dag(project_dir, Path(spec))

    result = asyncio.run(_run())

    click.echo()
    click.echo(f"Nodes: {result.total_nodes} | Converged: {result.converged} | Stuck: {result.stuck}")
    click.echo(f"Total iterations: {result.total_iterations}")
    for nid, status in result.node_results.items():
        icon = "✅" if status.value == "converged" else "❌"
        click.echo(f"  {icon} {nid}: {status.value}")


@cli.command()
@click.option("--project", required=True, type=click.Path(), help="Project directory to initialize")
def init(project):
    import yaml
    project_dir = Path(project)
    project_dir.mkdir(parents=True, exist_ok=True)

    config_path = project_dir / "asr_config.yaml"
    config = create_default_config()
    config_path.write_text(yaml.dump(config.model_dump(), default_flow_style=False))

    runtime_dir = project_dir / ".runtime"
    for sub in ["events", "inbox/builder", "inbox/tester", "inbox/analyzer",
                "tasks", "patches", "diffs", "state", "logs"]:
        (runtime_dir / sub).mkdir(parents=True, exist_ok=True)

    # Generate example DESIGN.md
    design_path = project_dir / "DESIGN.md"
    if not design_path.exists():
        design_path.write_text(_EXAMPLE_DESIGN_MD)

    # Generate opencode config template (opencode 2.x: per-repo `opencode.json`
    # at project root; the `.opencode/` directory is only for agents/commands/plugins)
    opencode_config = project_dir / "opencode.json"
    if not opencode_config.exists():
        opencode_config.write_text(_EXAMPLE_OPENCODE_CONFIG)

    click.echo(f"Initialized MoDou project at {project_dir}")
    click.echo(f"  Config:       {config_path}")
    click.echo(f"  Design doc:   {design_path} (edit this to define your project)")
    click.echo(f"  Runtime:      {runtime_dir}")
    click.echo(f"  OpenCode:     {opencode_config} (configure your LLM provider here)")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. Edit {design_path} — describe your project")
    click.echo(f"  2. Edit {opencode_config} — configure your LLM provider/model")
    click.echo(f"  3. Run:  asr run --project {project}")


_EXAMPLE_DESIGN_MD = """\
# Project Name

## Overview
Brief description of what this project does.

## Architecture
Describe the system architecture, module structure, and key design decisions.

## Modules

### module_1
- **Purpose**: What this module does
- **Files**: `src/module_1.py`
- **Key functions**: function_a, function_b

### module_2
- **Purpose**: What this module does
- **Files**: `src/module_2.py`
- **Key functions**: function_c, function_d

## Data Models
Describe any data structures, schemas, or database tables.

## API Endpoints
If applicable, list API endpoints and their behavior.

## Constraints
- Constraint 1
- Constraint 2

## Testing Strategy
Describe how the project should be tested.
"""

_EXAMPLE_OPENCODE_CONFIG = """\
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local LLM",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "empty"
      },
      "models": {
        "glm-4.7-fp8": {
          "name": "glm-4.7-fp8",
          "limit": {
            "context": 262144,
            "output": 32768
          }
        }
      }
    }
  },
  "model": "local/glm-4.7-fp8",
  "permission": {
    "edit": "allow",
    "bash": {
      "*": "allow",
      "rm -rf *": "deny"
    }
  }
}
"""


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory to diagnose")
def doctor(project):
    """Diagnose MoDou environment and configuration."""
    import shutil as _shutil
    import subprocess as _sp

    project_dir = Path(project).resolve()
    ok = True

    click.echo("MoDou Environment Diagnosis")
    click.echo("=" * 50)

    # 1. Python version
    import sys as _sys
    click.echo(f"Python: {_sys.version.split()[0]} ", nl=False)
    if _sys.version_info >= (3, 11):
        click.echo("OK")
    else:
        click.echo("FAIL (need >= 3.11)")
        ok = False

    # 2. opencode CLI (version check: MoDou requires opencode >= 2)
    click.echo("opencode CLI: ", nl=False)
    opencode_path = _shutil.which("opencode")
    if opencode_path:
        version = ""
        try:
            ver = _sp.run([opencode_path, "--version"], capture_output=True, text=True, timeout=5)
            version = ver.stdout.strip()
        except Exception:
            click.echo(f"OK (at {opencode_path})")
        if version:
            try:
                major = int(version.lower().split("v")[-1].split(".")[0])
            except ValueError:
                major = 2  # unparseable — assume current
            if major >= 2:
                click.echo(f"OK ({version})")
            else:
                click.echo(f"FAIL ({version} — MoDou requires opencode >= 2: --dir and --dangerously-skip-permissions were removed)")
                ok = False
    else:
        click.echo("MISSING (npm install -g opencode-ai)")
        ok = False

    # 3. pytest
    click.echo("pytest: ", nl=False)
    pytest_path = _shutil.which("pytest")
    if pytest_path:
        click.echo("OK")
    else:
        click.echo("MISSING (pip install pytest)")
        ok = False

    # 4. Project structure
    design = project_dir / "DESIGN.md"
    click.echo(f"DESIGN.md: ", nl=False)
    if design.exists():
        size = design.stat().st_size
        click.echo(f"OK ({size} bytes)")
    else:
        click.echo("MISSING (asr init --project . to generate)")

    # 5. opencode config (opencode 2.x: per-repo `opencode.json`; legacy
    #    `.opencode/config.json` still honoured as fallback)
    oc_config = project_dir / "opencode.json"
    if not oc_config.exists():
        oc_config = project_dir / ".opencode" / "config.json"
    if not oc_config.exists():
        oc_config = Path.home() / ".config" / "opencode" / "opencode.json"
    click.echo(f"opencode config: ", nl=False)
    if oc_config.exists():
        click.echo(f"OK ({oc_config})")
    else:
        click.echo("MISSING (asr init --project . to generate template)")

    # 6. asr_config.yaml
    asr_config = project_dir / "asr_config.yaml"
    click.echo(f"asr_config.yaml: ", nl=False)
    if asr_config.exists():
        click.echo("OK")
    else:
        click.echo("not found (will use defaults)")

    # 7. Runtime directories
    runtime = project_dir / ".runtime"
    click.echo(f".runtime/: ", nl=False)
    if runtime.exists():
        click.echo("OK")
    else:
        click.echo("will be auto-created on first run")

    click.echo("=" * 50)
    if ok:
        click.echo("All checks passed. Ready to run: asr run --project .")
    else:
        click.echo("Some checks failed. Fix the issues above before running.")


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def _show_progress(result) -> None:
    from asr.events.models import EventType
    events = result.events

    tests = [e for e in events if e.type in (EventType.TEST_FAILED, EventType.TEST_PASSED, EventType.TEST_ERROR)]
    patches = [e for e in events if e.type == EventType.PATCH_GENERATED]
    applied = [e for e in events if e.type == EventType.PATCH_APPLIED]
    success = [e for e in applied if e.payload.get("success")]

    click.echo(f"Tests: {len(tests)} | Patches: {len(patches)} | Applied: {len(success)}/{len(applied)}")

    test_errors = [e for e in tests if e.type == EventType.TEST_ERROR]
    if test_errors:
        click.echo(f"\n⚠️  编译/Lint 错误 ({len(test_errors)} 次):")
        shown = set()
        for e in test_errors:
            msg = e.payload.get("error_message", "")[:150]
            if msg not in shown:
                shown.add(msg)
                click.echo(f"  {msg}")

    iterations = [e for e in events if e.type == EventType.CONVERGENCE_ITERATION]
    if iterations:
        click.echo(f"\n{'Iter':<6} {'Errors':<8} {'Phase':<12} {'Detail'}")
        click.echo("-" * 65)
        for it in iterations:
            p = it.payload if isinstance(it.payload, dict) else {}
            errs = p.get("errors_remaining", "?")
            phase = p.get("phase", "")
            detail = p.get("detail", "")
            click.echo(f"{p.get('iteration', '?'):<6} {str(errs):<8} {phase:<12} {detail}")

    last_test = tests[-1] if tests else None
    if last_test and last_test.type == EventType.TEST_FAILED:
        failures = last_test.payload.get("failures", [])
        if failures:
            click.echo(f"\n剩余测试失败 ({len(failures)}):")
            for f in failures[:10]:
                click.echo(f"  ❌ {f.get('nodeid', '?'):<40} {f.get('message', '?')[:60]}")

    last_patch = patches[-1] if patches else None
    if last_patch:
        diff = last_patch.payload.get("diff_text", "")
        if diff:
            click.echo(f"\n最后一次补丁 (前 200 字符):")
            for line in diff.split("\n")[:6]:
                click.echo(f"  {line[:100]}")


if __name__ == "__main__":
    cli()
