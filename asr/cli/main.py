from __future__ import annotations

import asyncio
from pathlib import Path

import click

from asr.agents.llm_tracker import get_agent_tokens
from asr.config.loader import create_default_config, load_config
from asr.runtime import ASRRuntime

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
    # Pre-flight: check opencode availability (environment error → exit 2)
    import shutil as _shutil

    from asr.controller.convergence import ConvergenceState
    if not _shutil.which("opencode"):
        click.echo("ERROR: 'opencode' CLI not found in PATH.")
        click.echo("Install it:  npm install -g opencode-ai")
        click.echo("Configure:   asr init --project .  (generates opencode.json template)")
        raise SystemExit(EXIT_ENV_ERROR)

    try:
        config = load_config(config_path) if config_path else create_default_config()
    except Exception as exc:
        click.echo(f"ERROR: failed to load config: {exc}")
        raise SystemExit(EXIT_ENV_ERROR) from None
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
        raise SystemExit(EXIT_ENV_ERROR) from None

    click.echo()
    dag_info = result.summary.get("dag", {}) if result.summary else {}
    if dag_info:
        click.echo(f"Mode: DAG | Nodes: {dag_info.get('total_nodes', '?')} | Converged: {dag_info.get('converged', '?')} | Stuck: {dag_info.get('stuck', '?')}")
        if result.state == ConvergenceState.CONVERGED:
            click.echo("✅ CONVERGED — all DAG nodes converged")
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
        click.echo("\n📁 详细日志: .runtime/logs/asr.log")
        click.echo("📁 LLM 追踪: .runtime/logs/llm.jsonl")

    raise SystemExit(exit_code)


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory under assessment")
@click.option("--spec", default=None, type=click.Path(exists=True), help="Spec YAML file (optional; reads DESIGN.md if omitted)")
@click.option("--no-analyzer", is_flag=True, help="Skip semantic analysis; deterministic verification only")
@click.option("--enable-verifier", "verifiers", multiple=True,
              help="Deterministic verifier plugin(s) to run (e.g. compile_check; Experimental)")
@click.option("--json-output", default=None, type=click.Path(), help="Write structured Assessment to a JSON file")
@click.option("--decision-ref", default=None,
              help="Opaque decision anchor (MOSS-owned format) recorded on the assessment/feedback")
@click.option("--feedback-dir", default=None, type=click.Path(),
              help="Write a modou/feedback@1 envelope for MOSS here (pull-based decision channel; Experimental)")
@click.option("--timeout", default=600, type=int, help="pytest timeout in seconds")
@click.option("--basetemp", default=None, type=click.Path(), help="pytest basetemp (use a workspace path under sandboxes)")
def assess(project, spec, no_analyzer, verifiers, json_output,
           decision_ref, feedback_dir, timeout, basetemp):
    """独立评估：Tester 验证 + Analyzer 偏差分析。不调用 Builder，不修改交付物。

    Exit codes: 0=PASS, 1=DEVIATION/INCONCLUSIVE, 2=BLOCKED/environment error.
    """
    import json as _json

    from asr.assurance import STATUS_BLOCKED, STATUS_PASS
    from asr.assurance import assess as run_assessment

    project_dir = Path(project).resolve()
    spec_path = Path(spec).resolve() if spec else None
    basetemp_path = Path(basetemp).resolve() if basetemp else None

    result = asyncio.run(run_assessment(
        project_dir,
        spec_path,
        use_analyzer=not no_analyzer,
        timeout=timeout,
        basetemp=basetemp_path,
        verifier_names=list(verifiers) or None,
    ))
    if decision_ref:
        result.decision_ref = decision_ref

    click.echo(result.to_text())
    if json_output:
        out = Path(json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        click.echo(f"\n📄 Assessment JSON: {out}")
    if feedback_dir:
        from asr.assurance.decision_feedback import (
            feedback_from_assessment_dict,
            write_feedback,
        )
        env = feedback_from_assessment_dict(result.to_dict())
        fb_path = write_feedback(env, feedback_dir)
        click.echo(f"📮 Decision feedback: {fb_path} (modou/feedback@1)")

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
@click.option("--decision-ref", default=None,
              help="Opaque decision anchor (MOSS-owned format) stamped onto feedback envelopes")
@click.option("--feedback-dir", default=None, type=click.Path(),
              help="Write modou/feedback@1 envelopes per assessment for MOSS (pull-based; Experimental)")
@click.option("--ledger", default=None, type=click.Path(),
              help="PM quality ledger for envelope confidence (default .modou/quality-ledger.jsonl)")
def board_assess(board, as_of, designs, item_ids, sample_rate, rules_file,
                 rules_ref, no_llm, workdir, store_dir, reassess_of,
                 converge_report, json_output, decision_ref, feedback_dir, ledger):
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
            decision_ref=decision_ref,
            feedback_dir=feedback_dir,
            ledger_path=ledger,
        ))
    except (SnapshotError, ValueError) as e:
        click.echo(f"BLOCKED: {e}")
        raise SystemExit(EXIT_ENV_ERROR) from None

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
    if feedback_dir:
        click.echo(f"📮 Decision feedback: {len(report.assessments)} envelope(s) → {feedback_dir} (modou/feedback@1)")

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


@cli.command(name="board-calibrate")
@click.option("--suite", required=True, type=click.Path(),
              help="Calibration suite dir (built on first run: injection board + expected.json)")
@click.option("--workdir", default=None, type=click.Path(), help="Assessor staging dir")
@click.option("--store-dir", default=None, type=click.Path(), help="Assessment archive dir")
@click.option("--ledger", default=None, type=click.Path(), help="PM quality ledger file (default .modou/quality-ledger.jsonl)")
@click.option("--rules-file", default=None, type=click.Path(exists=True),
              help="OuSheng rules doc injected into every dossier (recommended: governance semantics need the rulebook)")
@click.option("--rules-ref", default=None, help="Version ref for the rules doc")
@click.option("--rounds", default=1, type=int, help="Calibration rounds; ALL rounds must pass (LLM nondeterminism guard, use 3 for real calibration)")
@click.option("--json-output", default=None, type=click.Path(), help="Write calibration result JSON here")
def board_calibrate(suite, workdir, store_dir, ledger, rules_file, rules_ref,
                    rounds, json_output):
    """校准：对注入集跑完整管线，对照 ground truth 出指标与门槛裁决。

    门槛（P2 冻结）：注入检出率 100%、正常样本误报 0、不确定纪律零违例
    （--rounds N 时全部轮次通过才算过）；precision 门槛由 PM 标记账本
    单独判定（≥0.8 且 n≥20）。校准不通过 = 停在影子模式（合法终态）。
    """
    import json as _json

    from asr.assurance.calibration import (
        Thresholds,
        build_calibration_suite,
        evaluate,
    )
    from asr.assurance.pipeline import run_board_assessment
    from asr.assurance.store import AssessmentStore

    suite_dir = Path(suite)
    if not (suite_dir / "board").exists():
        build_calibration_suite(suite_dir)
        click.echo(f"Built injection suite at {suite_dir}")
    truth = _json.loads((suite_dir / "expected.json").read_text())
    item_ids = [c["id"] for c in truth["cases"] if c.get("kind", "item") == "item"]

    th = Thresholds()
    all_rounds: list[dict] = []
    ok = True
    for rnd in range(1, max(1, rounds) + 1):
        report = asyncio.run(run_board_assessment(
            board_dir=suite_dir / "board",
            items=item_ids,
            rules_file=rules_file, rules_ref=rules_ref,
            workdir=workdir,
            store=AssessmentStore(Path(store_dir) if store_dir
                                  else Path(".modou/calib-assessments")),
        ))
        metrics = evaluate(report.to_dict(), truth)
        checks = th.check(metrics)
        rnd_ok = all(c["pass"] for c in checks)
        ok = ok and rnd_ok
        all_rounds.append({"round": rnd, "metrics": metrics.to_dict(),
                           "checks": checks, "pass": rnd_ok})
        if rounds > 1:
            click.echo(f"— Round {rnd}/{rounds}: "
                       f"detection={metrics.detection_rate():.0%} "
                       f"fp={len(metrics.false_positives)} "
                       f"violations={len(metrics.inconclusive_violations)} "
                       f"[{'PASS' if rnd_ok else 'FAIL'}]")
        last = metrics

    click.echo(f"Detection rate: {last.detection_rate():.0%} "
               f"({len(last.expected_hits)}/{last.injections_total})")
    if last.expected_misses:
        click.echo("  MISSES: " + "; ".join(last.expected_misses))
    click.echo(f"False positives on normals: {len(last.false_positives)}/{last.normal_total}"
               + (f" → {last.false_positives}" if last.false_positives else ""))
    if last.inconclusive_violations:
        click.echo("INCONCLUSIVE discipline violations: " + "; ".join(last.inconclusive_violations))
    from asr.assurance.calibration import QualityLedger
    led = QualityLedger(Path(ledger) if ledger else Path(".modou/quality-ledger.jsonl"))
    n, p = led.precision()
    click.echo(f"PM-marked precision: {p if p is None else f'{p:.0%}'} (n={n})")
    click.echo("-" * 60)
    pcheck = th.precision_check(led)
    ok = ok and pcheck["pass"]
    for c in all_rounds[-1]["checks"]:
        click.echo(f"[{'PASS' if c['pass'] else 'FAIL'}] {c['criterion']}: "
                   f"{c['value']} (required {c['required']})")
    click.echo(f"[{'PASS' if pcheck['pass'] else 'FAIL'}] {pcheck['criterion']}: "
               f"{pcheck['value']} (required {pcheck['required']})")
    verdict = "CALIBRATED — writeback may be enabled (P3, still needs PM authorization)" if ok \
        else "NOT CALIBRATED — stay in shadow mode (legal terminal state)"
    click.echo(verdict)

    if json_output:
        out = Path(json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps({
            "rounds": all_rounds, "precision_check": pcheck, "calibrated": ok,
        }, ensure_ascii=False, indent=2))
        click.echo(f"\n📄 Calibration JSON: {out}")
    raise SystemExit(0 if ok else 1)


@cli.command(name="board-writeback")
@click.option("--assessment", required=True, type=click.Path(exists=True), help="Assessment JSON to write back")
@click.option("--board", required=True, type=click.Path(exists=True), help="OuSheng board clone (writable clone; NEVER the shared production checkout)")
@click.option("--execute", is_flag=True, help="Actually create items (default: dry-run, print plan only)")
@click.option("--actor", default="modou", help="Acting identity (must be a registered agent actor)")
@click.option("--corrections-ledger", default=None, type=click.Path(), help="Corrections ledger (default .modou/corrections.jsonl)")
@click.option("--quality-ledger", default=None, type=click.Path(), help="PM quality ledger for fp blacklist (default .modou/quality-ledger.jsonl)")
@click.option("--min-severity", default="medium", type=click.Choice(["critical", "high", "medium", "low"]))
@click.option("--max-items", default=5, type=int, help="Per-run correction budget")
def board_writeback(assessment, board, execute, actor, corrections_ledger,
                    quality_ledger, min_severity, max_items):
    """纠偏写回（P3）：把过门 findings 计划为纠偏 WorkItem，经官方 MCP 创建。

    默认 dry-run。--execute 才实写；纠偏项一律 backlog（PM 分诊=授权）。
    幂等：同 fingerprint 的 OPEN 纠偏项不重复创建；PM 标 fp 的指纹永不写回。
    失败记 PENDING 停止——绝不盲目重试 create。

    Exit codes: 0=all planned/created, 2=any failure or blocked.
    """
    import json as _json

    from asr.adapters.ousheng.mcp_client import OuShengMCPClient
    from asr.adapters.ousheng.snapshot import load_snapshot
    from asr.assurance.feedback import (
        CorrectionsLedger,
        WritebackPolicy,
        execute_writeback,
        false_positive_fingerprints,
        plan_writeback,
        subject_systems_from_snapshot,
    )

    data = _json.loads(Path(assessment).read_text())
    if "assessment_id" not in data or "findings" not in data:
        # A board-assess --json-output *report* ({"assessments": [...]}) is the
        # most common wrong input; name the actual store file instead.
        click.echo(
            "ERROR: --assessment expects a single assessment JSON (an ASM-*.json "
            f"from the assessment store), got keys: {sorted(data)[:6]}")
        raise SystemExit(EXIT_ENV_ERROR)
    snap = load_snapshot(Path(board))
    systems = subject_systems_from_snapshot(snap)
    data.setdefault("scope", {})["subject_systems"] = systems

    policy = WritebackPolicy(min_severity=min_severity, max_items=max_items)
    cledger = CorrectionsLedger(Path(corrections_ledger) if corrections_ledger
                                else Path(".modou/corrections.jsonl"))
    plan = plan_writeback(data, policy,
                          open_fingerprints=cledger.open_fingerprints(),
                          fp_fingerprints=false_positive_fingerprints(
                              Path(quality_ledger) if quality_ledger
                              else Path(".modou/quality-ledger.jsonl")))

    mode = "EXECUTE" if execute else "DRY-RUN"
    click.echo(f"Writeback {mode} | assessment={data['assessment_id']} "
               f"| findings={len(data.get('findings', []))} plan={len(plan)} "
               f"(min_sev={min_severity}, budget={max_items})")
    if not plan:
        click.echo("Nothing eligible (all filtered, deduped or fp-blacklisted).")
        raise SystemExit(0)
    for c in plan:
        click.echo(f"  · [{c.severity}] {c.code} @ {c.subject} ({c.system})")
        click.echo(f"      {c.title}")

    if not execute:
        click.echo("Dry-run only. Re-run with --execute after review.")
        raise SystemExit(0)

    with OuShengMCPClient(str(Path(board).resolve())) as client:
        results = execute_writeback(plan, client, actor, cledger,
                                    board=str(snap.project.get("id") or board),
                                    policy=policy, dry_run=False)
    failed = [c for c in results if c.state != "CREATED"]
    for c in results:
        click.echo(f"  {'✓' if c.state == 'CREATED' else '✗'} {c.new_id or '-'} "
                   f"{c.code}@{c.subject} → {c.state}")
    if failed:
        click.echo(f"{len(failed)} correction(s) failed — recorded PENDING, "
                   f"investigate before retrying (at-most-once).")
        raise SystemExit(2)
    click.echo(f"All {len(results)} correction item(s) created in backlog — "
               f"PM triage is the authorization step.")
    raise SystemExit(0)


@cli.command(name="board-watch")
@click.option("--board", required=True, type=click.Path(exists=True), help="OuSheng board clone (read-only; watch fetches, never pushes)")
@click.option("--store-dir", default=None, type=click.Path(), help="Assessment archive (default .modou/assessments)")
@click.option("--interval", default=3600.0, type=float, help="Seconds between cycles (default 1h)")
@click.option("--once", is_flag=True, help="Run a single cycle and exit (cron mode)")
@click.option("--no-fetch", is_flag=True, help="Skip git fetch, assess working HEAD (offline/tests)")
@click.option("--design", "designs", multiple=True, help="Design topic(s); default all decided")
@click.option("--sample-rate", default=0.0, type=float, help="T1 sampling rate")
@click.option("--rules-file", default=None, type=click.Path(exists=True))
@click.option("--rules-ref", default=None)
@click.option("--workdir", default=None, type=click.Path())
@click.option("--feedback-dir", default=None, type=click.Path(),
              help="Write modou/feedback@1 envelopes per cycle for MOSS (Experimental)")
@click.option("--ledger", default=None, type=click.Path(),
              help="PM quality ledger for envelope confidence (default .modou/quality-ledger.jsonl)")
def board_watch(board, store_dir, interval, once, no_fetch, designs,
                sample_rate, rules_file, rules_ref, workdir,
                feedback_dir, ledger):
    """定时影子巡检（P4）：fetch→定 sha→评估→存档→趋势账本，循环。

    无人值守纪律：每周期隔离，任何失败记入周期日志后继续（BLOCKED 是周期
    结果不是崩溃）。cruise/PM 读 last-run.json 作非阻塞观察信号。
    """
    import asyncio as _aio

    from asr.assurance.store import AssessmentStore
    from asr.assurance.watch import run_cycle, watch_loop

    store = AssessmentStore(Path(store_dir) if store_dir else Path(".modou/assessments"))
    kwargs = dict(fetch=not no_fetch, designs=list(designs) or None,
                  sample_rate=sample_rate, rules_file=rules_file,
                  rules_ref=rules_ref, workdir=workdir,
                  feedback_dir=feedback_dir,
                  ledger_path=ledger or ".modou/quality-ledger.jsonl")
    if once:
        s = _aio.run(run_cycle(1, Path(board).resolve(), store, **kwargs))
        click.echo(f"cycle {s.cycle} rev={s.rev[:10]} subjects={len(s.statuses)} "
                   f"new={len(s.new_fingerprints)} recurred={len(s.recurred_fingerprints)} "
                   f"errors={len(s.errors)}")
        if s.errors:
            click.echo("; ".join(s.errors[:3]))
        raise SystemExit(2 if s.errors else (1 if any(
            v in ("DEVIATION", "INCONCLUSIVE") for v in s.statuses.values()) else 0))
    _aio.run(watch_loop(Path(board).resolve(), store, interval, **kwargs))


@cli.command(name="board-trend")
@click.option("--store-dir", default=None, type=click.Path(), help="Assessment archive (default .modou/assessments)")
@click.option("--json-output", default=None, type=click.Path())
def board_trend(store_dir, json_output):
    """趋势汇总：状态分布、偏差代码直方、复发与解决指纹（口径显式）。"""
    import json as _json

    from asr.assurance.store import AssessmentStore
    from asr.assurance.watch import compute_trend

    store = AssessmentStore(Path(store_dir) if store_dir else Path(".modou/assessments"))
    t = compute_trend(store)
    click.echo(f"Subjects: {t['subjects']}  Runs: {t['runs']}")
    click.echo(f"Status (latest/subject): {t['status_histogram_latest']}")
    click.echo(f"Codes (latest/subject):  {t['code_histogram_latest']}")
    click.echo(f"Recurred: {len(t['recurred_fingerprints'])}  "
               f"Resolved: {len(t['resolved_fingerprints'])}")
    if json_output:
        out = Path(json_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(t, ensure_ascii=False, indent=2))
        click.echo(f"📄 Trend JSON: {out}")


@cli.command(name="feedback-export")
@click.option("--assessment", required=True, type=click.Path(exists=True),
              help="Stored/exported assessment JSON (mode A or mode C format)")
@click.option("--out-dir", default=None, type=click.Path(),
              help="Feedback dir (default .modou/feedback)")
@click.option("--decision-ref", default=None,
              help="Decision anchor override (else taken from the assessment)")
@click.option("--ledger", default=None, type=click.Path(),
              help="PM quality ledger for confidence (default .modou/quality-ledger.jsonl)")
def feedback_export(assessment, out_dir, decision_ref, ledger):
    """决策反馈导出（Experimental）：把既有 assessment 转成 modou/feedback@1 信封。

    MOSS 接入期不必重跑评估——对存档/导出的 Assessment JSON 逐份追溯生成。
    仅路由决策层 findings（BASELINE_DEFICIENT/SPEC_INCOMPLETE/TARGET_MISS）。
    """
    import json as _json

    from asr.assurance.decision_feedback import (
        feedback_from_assessment_dict,
        write_feedback,
    )

    data = _json.loads(Path(assessment).read_text())
    env = feedback_from_assessment_dict(
        data, decision_ref=decision_ref, ledger_path=ledger or ".modou/quality-ledger.jsonl")
    path = write_feedback(env, out_dir or ".modou/feedback")
    click.echo(f"📮 {env.feedback_id} → {path}")
    click.echo(f"  decision_ref={env.decision_ref} status={env.deliverable_accepted} "
               f"decision_findings={len(env.decision_findings)}")


@cli.command(name="board-mark")
@click.option("--assessment", required=True, type=click.Path(exists=True), help="Assessment JSON file")
@click.option("--finding-index", default=None, type=int, help="Index into findings[] (0-based)")
@click.option("--fingerprint", default=None, help="Finding fingerprint (alternative to index)")
@click.option("--verdict", required=True, type=click.Choice(["tp", "fp"]), help="tp=true positive, fp=false positive")
@click.option("--by", "marked_by", required=True, help="Marking human (e.g. pm)")
@click.option("--note", default="", help="Optional note")
@click.option("--ledger", default=None, type=click.Path(), help="Ledger file (default .modou/quality-ledger.jsonl)")
def board_mark(assessment, finding_index, fingerprint, verdict, marked_by, note, ledger):
    """PM 抽检标记：对某条 finding 记 tp/fp，进质量账本（MODOU 本地，不写 board）。"""
    import json as _json

    from asr.assurance.calibration import QualityLedger

    data = _json.loads(Path(assessment).read_text())
    findings = data.get("findings", [])
    if fingerprint is None:
        if finding_index is None or not (0 <= finding_index < len(findings)):
            raise SystemExit(f"ERROR: provide --finding-index (0..{len(findings) - 1}) or --fingerprint")
        fingerprint = findings[finding_index].get("fingerprint")
    if not fingerprint:
        raise SystemExit("ERROR: finding has no fingerprint; re-run assessment (P1 pipeline stamps them)")

    led = QualityLedger(Path(ledger) if ledger else Path(".modou/quality-ledger.jsonl"))
    led.mark(fingerprint, verdict, marked_by, note)
    n, p = led.precision()
    click.echo(f"Marked {fingerprint} as {verdict} by {marked_by}. "
               f"Ledger: n={n}, precision={p if p is None else f'{p:.0%}'}")


@cli.command(name="run-dag")
@click.option("--project", required=True, type=click.Path(exists=True), help="Project directory")
@click.option("--spec", required=True, type=click.Path(exists=True), help="Spec YAML file")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True), help="Config YAML file")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def run_dag(project, spec, config_path, verbose):
    config = load_config(config_path) if config_path else create_default_config()
    project_dir = Path(project).resolve()
    runtime = ASRRuntime(config)

    click.echo("MoDou Runtime [DAG Mode]")
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
      "rm -rf *": "deny",
      "sudo *": "deny",
      "git push *": "deny",
      "chmod -R 777 *": "deny"
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
    if _sys.version_info >= (3, 11):  # noqa: UP036 — doctor is a diagnostic tool; the redundant check is its job
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
    click.echo("DESIGN.md: ", nl=False)
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
    click.echo("opencode config: ", nl=False)
    if oc_config.exists():
        click.echo(f"OK ({oc_config})")
    else:
        click.echo("MISSING (asr init --project . to generate template)")

    # 6. asr_config.yaml — warn on persisted plaintext API keys (they belong
    #    in .env or opencode's config, never inside the project tree)
    asr_config = project_dir / "asr_config.yaml"
    click.echo("asr_config.yaml: ", nl=False)
    if asr_config.exists():
        try:
            import yaml as _yaml
            raw_cfg = _yaml.safe_load(asr_config.read_text()) or {}
            exposed = [
                a.get("role", "?")
                for a in raw_cfg.get("agents", [])
                if isinstance(a.get("model"), dict)
                and a["model"].get("api_key") not in (None, "", "empty")
                and not str(a["model"].get("api_key", "")).startswith("os.environ/")
            ]
            if exposed:
                click.echo("WARNING — plaintext api_key")
                click.echo(f"  agents with inline api_key: {', '.join(exposed)}")
                click.echo("  move it to .env (gitignored) or 'os.environ/NAME' indirection;")
                click.echo("  see git history risk if this file was ever committed")
            else:
                click.echo("OK")
        except Exception:
            click.echo("WARN (unparseable — defaults will be used)")
    else:
        click.echo("not found (will use defaults)")

    # 7. Runtime directories
    runtime = project_dir / ".runtime"
    click.echo(".runtime/: ", nl=False)
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
            click.echo("\n最后一次补丁 (前 200 字符):")
            for line in diff.split("\n")[:6]:
                click.echo(f"  {line[:100]}")


if __name__ == "__main__":
    cli()
