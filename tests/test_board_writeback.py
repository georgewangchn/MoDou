"""P3 writeback tests: policy planning, idempotency, MCP protocol, CLI."""
import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from asr.assurance.feedback import (
    CorrectionsLedger,
    WritebackPolicy,
    execute_writeback,
    false_positive_fingerprints,
    plan_writeback,
    subject_systems_from_snapshot,
)
from asr.cli.main import cli

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


def make_assessment(tmp: Path, findings=None) -> Path:
    findings = findings if findings is not None else [
        {"code": "EVIDENCE_GAP", "severity": "high", "confidence": 0.9,
         "subject": {"work_item": "T-101", "revision": 1},
         "expected": "e", "actual": "a", "recommendation": "r",
         "evidence_refs": ["tasks/T-B"], "fingerprint": "sha256:aaa"},
        {"code": "TASK_COVERAGE_GAP", "severity": "low", "confidence": 0.7,
         "subject": {"work_item": None}, "expected": "e2", "actual": "a2",
         "recommendation": "r2", "evidence_refs": ["baseline/x/design.md"],
         "fingerprint": "sha256:bbb"},
        {"code": "FALSE_COMPLETION", "severity": "critical", "confidence": 0.95,
         "subject": {"work_item": "T-102", "revision": 2},
         "expected": "e3", "actual": "a3", "recommendation": "r3",
         "evidence_refs": ["tasks/T-C"], "fingerprint": "sha256:ccc"},
    ]
    p = tmp / "asm.json"
    p.write_text(json.dumps({
        "schema_version": "modou/assessment@1.0", "assessment_id": "ASM-TEST",
        "kind": "item", "subject": "T-101", "status": "DEVIATION",
        "scope": {"board": "synth-board", "as_of_commit": "HEAD",
                  "subject_systems": {"T-101": "data", "T-102": "data"}},
        "findings": findings}, ensure_ascii=False))
    return p


def test_plan_filters_severity_and_budget():
    import pathlib
    import tempfile

    from asr.adapters.ousheng.snapshot import load_snapshot
    from tests.board_fixture import build_board
    with tempfile.TemporaryDirectory() as td:
        snap = load_snapshot(build_board(pathlib.Path(td)))
        systems = subject_systems_from_snapshot(snap)
        data = json.loads(make_assessment(pathlib.Path(td)).read_text())
        data["scope"]["subject_systems"] = systems
        plan = plan_writeback(data, WritebackPolicy(), set(), set())
        # low filtered; high + critical kept, budget 5
        assert [c.fingerprint for c in plan] == ["sha256:aaa", "sha256:ccc"]
        assert plan[0].system == "data" and "[MODOU]" in plan[0].title
        assert "fingerprint=sha256:aaa" in plan[0].description
        # budget
        plan1 = plan_writeback(data, WritebackPolicy(max_items=1), set(), set())
        assert len(plan1) == 1


def test_plan_dedup_and_fp_blacklist(tmp_path):
    data = json.loads(make_assessment(tmp_path).read_text())
    data["scope"]["subject_systems"] = {"T-101": "data", "T-102": "data", "*": "data"}
    plan = plan_writeback(data, WritebackPolicy(),
                          open_fingerprints={"sha256:aaa"},
                          fp_fingerprints={"sha256:ccc"})
    assert [c.fingerprint for c in plan] == []  # aaa open, ccc blacklisted


def test_no_system_means_report_only(tmp_path):
    data = json.loads(make_assessment(tmp_path).read_text())
    data["scope"]["subject_systems"] = {}
    assert plan_writeback(data, WritebackPolicy(), set(), set()) == []


def test_ledger_ids_and_states(tmp_path):
    led = CorrectionsLedger(tmp_path / "c.jsonl")
    assert led.open_fingerprints() == set()
    assert led.next_id(WritebackPolicy()) == "MD-900"
    from asr.assurance.feedback import Correction
    c = Correction(fingerprint="f1", code="X", severity="high", subject="T-101",
                   system="data", title="t", description="d", assessment_id="A",
                   new_id="MD-900")
    led.append(c, "OPEN", board="b")
    assert led.open_fingerprints() == {"f1"}
    assert led.next_id(WritebackPolicy()) == "MD-901"


def test_fp_fingerprints_from_quality_ledger(tmp_path):
    q = tmp_path / "q.jsonl"
    q.write_text(json.dumps({"fingerprint": "f1", "verdict": "fp"}) + "\n"
                 + json.dumps({"fingerprint": "f2", "verdict": "tp"}) + "\n")
    assert false_positive_fingerprints(q) == {"f1"}


# ── MCP client against fake server ──────────────────────────────────────────
def test_mcp_client_protocol(tmp_path):
    from asr.adapters.ousheng.mcp_client import OuShengMCPClient
    logf = tmp_path / "mcp.log"
    with OuShengMCPClient(str(tmp_path), cmd=[sys.executable, FAKE, str(logf)]) as c:
        tools = c.list_tools()
        assert tools[0]["name"] == "create_work_item"
        res = c.call_tool("create_work_item", {"path": "/x", "id": "MD-900"})
        assert "ok:create_work_item" in res["content"][0]["text"]
    calls = [json.loads(l) for l in logf.read_text().splitlines()]
    init = [c for c in calls if c["method"] == "initialize"][0]
    assert init["params"]["clientInfo"]["name"] == "modou"
    create = [c for c in calls if c["method"] == "tools/call"][0]
    assert create["params"]["name"] == "create_work_item"
    assert create["params"]["arguments"]["id"] == "MD-900"


def test_mcp_client_tool_error_raises(tmp_path):
    from asr.adapters.ousheng.mcp_client import MCPError, OuShengMCPClient
    with OuShengMCPClient(str(tmp_path), cmd=[sys.executable, FAKE, str(tmp_path / "l")]) as c:
        with pytest.raises(MCPError, match="tool-error"):
            c.call_tool("create_work_item", {"fail": True})


def test_execute_writeback_at_most_once(tmp_path):
    from asr.adapters.ousheng.mcp_client import OuShengMCPClient
    data = json.loads(make_assessment(tmp_path).read_text())
    led = CorrectionsLedger(tmp_path / "c.jsonl")

    # failing server: corrections land PENDING, no retry storm
    class Failing(OuShengMCPClient):
        def call_tool(self, name, args):
            raise RuntimeError("timeout")

    plan = plan_writeback(data, WritebackPolicy(), set(), set())
    with Failing(str(tmp_path)) as c:
        results = execute_writeback(plan, c, "modou", led, "b",
                                    WritebackPolicy(), dry_run=False)
    assert all(r.state == "FAILED" for r in results)
    assert led.entries()[0]["state"] == "PENDING"

    # successful server
    plan2 = plan_writeback(data, WritebackPolicy(), set(), set())
    with OuShengMCPClient(str(tmp_path), cmd=[sys.executable, FAKE, str(tmp_path / "mcp.log")]) as c:
        results = execute_writeback(plan2, c, "modou", led, "b",
                                    WritebackPolicy(), dry_run=False)
    assert all(r.state == "CREATED" for r in results)
    # re-plan with the now-open fingerprints → empty (idempotent)
    assert plan_writeback(data, WritebackPolicy(), led.open_fingerprints(), set()) == []


def test_execute_half_done_recovery(tmp_path):
    """create fails but the item actually exists → recovered as CREATED, no duplicate."""
    from asr.adapters.ousheng.mcp_client import OuShengMCPClient
    data = json.loads(make_assessment(tmp_path).read_text())
    logf = tmp_path / "mcp.log"
    # pre-seed: MD-900 was already created in a previous half-failed run
    logf.write_text(json.dumps({"method": "tools/call", "params": {
        "name": "create_work_item", "arguments": {"id": "MD-900"}}}) + "\n")

    class Flaky(OuShengMCPClient):
        def call_tool(self, name, args):
            if name == "create_work_item" and args.get("id") == "MD-900":
                raise RuntimeError("connection lost after write")
            return super().call_tool(name, args)

    plan = plan_writeback(data, WritebackPolicy(), set(), set())
    led = CorrectionsLedger(tmp_path / "c.jsonl")
    with Flaky(str(tmp_path), cmd=[sys.executable, FAKE, str(logf)]) as c:
        results = execute_writeback(plan, c, "modou", led, "b",
                                    WritebackPolicy(), dry_run=False)
    assert results[0].state == "CREATED" and results[0].new_id == "MD-900"
    assert led.entries()[0]["state"] == "OPEN"
    # evidence pointer still attached post-recovery
    calls = [json.loads(l) for l in logf.read_text().splitlines()]
    assert any(c.get("params", {}).get("name") == "add_evidence"
               and c["params"]["arguments"]["work_id"] == "MD-900" for c in calls)


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_writeback_dry_run(tmp_path):
    from tests.board_fixture import build_board
    board = build_board(tmp_path / "b")
    asm = make_assessment(tmp_path)
    runner = CliRunner()
    r = runner.invoke(cli, ["board-writeback", "--assessment", str(asm),
                            "--board", str(board),
                            "--corrections-ledger", str(tmp_path / "c.jsonl")],
                      catch_exceptions=False)
    assert r.exit_code == 0
    assert "DRY-RUN" in r.output and "plan=2" in r.output
    assert "Dry-run only" in r.output
    assert not (tmp_path / "c.jsonl").exists()


def test_cli_writeback_execute_via_fake_mcp(tmp_path, monkeypatch):
    from tests.board_fixture import build_board
    board = build_board(tmp_path / "b")
    asm = make_assessment(tmp_path)
    monkeypatch.setenv("MODOU_MCP_CMD", f"{sys.executable} {FAKE} {tmp_path / 'mcp.log'}")
    runner = CliRunner()
    r = runner.invoke(cli, ["board-writeback", "--assessment", str(asm),
                            "--board", str(board), "--execute",
                            "--corrections-ledger", str(tmp_path / "c.jsonl")],
                      catch_exceptions=False)
    assert r.exit_code == 0
    assert "✓" in r.output and "backlog" in r.output
    calls = [json.loads(l) for l in (tmp_path / "mcp.log").read_text().splitlines()]
    tool_calls = [c for c in calls if c["method"] == "tools/call"]
    creates = [c for c in tool_calls if c["params"]["name"] == "create_work_item"]
    evidences = [c for c in tool_calls if c["params"]["name"] == "add_evidence"]
    assert len(creates) == 2  # budget respected; low finding excluded
    args = creates[0]["params"]["arguments"]
    assert args["id"] == "MD-900" and args["type"] == "bug"
    assert args["detected_by"] == "modou" and args["actor"] == "modou"
    assert "description" not in args  # T10: pointers only, no free-text surface
    # pointer evidence attached to each created item
    assert len(evidences) == 2
    ev = evidences[0]["params"]["arguments"]
    assert ev["type"] == "document" and ev["work_id"] == "MD-900"
    assert ev["locator"].startswith("modou-assessment://ASM-TEST#")
    assert ev["expect"] == 1


def test_cli_writeback_rejects_report_json_with_clear_error(tmp_path):
    """Regression (sim-lab S8): feeding a board-assess *report* JSON used to
    crash with KeyError('assessment_id'); it must fail loudly & readably."""
    from asr.cli.main import cli
    from tests.board_fixture import build_board

    board = build_board(tmp_path / "b")
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"assessments": [], "board": "x", "exit_code": 1}))
    runner = CliRunner()
    r = runner.invoke(cli, ["board-writeback", "--assessment", str(report),
                            "--board", str(board)], catch_exceptions=False)
    assert r.exit_code == 2
    assert "single assessment JSON" in r.output
    assert "assessments" in r.output  # names the wrong shape it received
