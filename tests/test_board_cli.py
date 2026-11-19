"""CLI tests for `asr board-assess` (plumbing mode + mocked-LLM pipeline)."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asr.cli.main import cli
from tests.board_fixture import build_board


@pytest.fixture()
def board(tmp_path):
    return build_board(tmp_path)


def _invoke(board, *args, workdir):
    runner = CliRunner()
    return runner.invoke(cli, [
        "board-assess", "--board", str(board),
        "--store-dir", str(workdir / "store"),
        "--json-output", str(workdir / "report.json"),
        *args,
    ], catch_exceptions=False)


def test_no_llm_plumbing_mode(tmp_path, board):
    r = _invoke(board, "--no-llm", workdir=tmp_path)
    assert r.output.splitlines()[0].startswith("Board: synth-board")
    assert "T0=6 T1=1" in r.output and "T2-skipped=3" in r.output
    # design dossier assessed → INCONCLUSIVE by construction; exit 1
    assert "[design:layered] INCONCLUSIVE" in r.output
    assert r.exit_code == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["analyzer_mode"].startswith("none")
    assert report["tier_coverage"] == {"T0": 6, "T1": 1, "T2": 3}
    assert report["assessments"][0]["status"] == "INCONCLUSIVE"
    # stored + indexed
    store_dir = tmp_path / "store"
    assert (store_dir / "index.jsonl").exists()
    assert len(report["assessments"]) == 1  # only the design job (no items/sample)


def test_no_llm_with_items_and_sample(tmp_path, board):
    r = _invoke(board, "--no-llm", "--sample-rate", "1.0",
                "--item", "T-105", workdir=tmp_path)
    # 1 design + 1 explicit item + T1 sample (BUG-201)
    assert r.output.count("[item:") == 2
    assert "[item:T-105] INCONCLUSIVE" in r.output
    assert "[item:BUG-201] INCONCLUSIVE" in r.output


def test_unknown_item_fails_loud(tmp_path, board):
    runner = CliRunner()
    r = runner.invoke(cli, ["board-assess", "--board", str(board),
                            "--no-llm", "--item", "NOPE-1",
                            "--store-dir", str(tmp_path / "s")])
    assert r.exit_code == 2
    assert "unknown work item" in r.output


def test_missing_board_dir(tmp_path):
    runner = CliRunner()
    r = runner.invoke(cli, ["board-assess", "--board", str(tmp_path / "nope"), "--no-llm"])
    assert r.exit_code == 2  # click path check or BLOCKED


def test_schema_drift_blocks(tmp_path, board):
    from tests.board_fixture import _git
    bad = board / ".ousheng/work/T-101.yaml"
    bad.write_text(bad.read_text().replace("schema_version: 2", "schema_version: 99", 1))
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "drift")
    r = _invoke(board, "--no-llm", workdir=tmp_path)
    assert r.exit_code == 2
    assert "BLOCKED" in r.output


@pytest.mark.asyncio
async def test_pipeline_llm_mode_mocked(tmp_path, board):
    from asr.assurance.pipeline import run_board_assessment
    from asr.assurance.store import AssessmentStore

    llm_text = (
        "[TASK_COVERAGE_GAP][HIGH][0.85] subject: layered | expected: 义务4 联合测试有集成证据 "
        "| actual: testing 态且无集成证据 | evidence: baseline/layered/design.md,tasks/T-104 "
        "| recommendation: 补充端到端集成证据\n"
        "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
        "obligations: 4\ncovered: 3\nseam: proven\noutcome: unproven\n"
    )
    with patch("asr.assurance.assessor.opencode_completion",
               return_value=(llm_text, 10, 20, 30)):
        report = await run_board_assessment(
            board, workdir=tmp_path / "stage",
            store=AssessmentStore(tmp_path / "store"))
    assert len(report.assessments) == 1
    a = report.assessments[0]
    assert a.status == "DEVIATION"
    assert a.findings[0].code == "TASK_COVERAGE_GAP"
    assert a.findings[0].fingerprint.startswith("sha256:")
    assert a.assurance["outcome"] == "INCONCLUSIVE"
    assert report.exit_code() == 1
    # archive round-trip
    store = AssessmentStore(tmp_path / "store")
    assert store.latest_for("design", "layered")["status"] == "DEVIATION"
    loaded = store.load(a.assessment_id)
    assert loaded["findings"][0]["code"] == "TASK_COVERAGE_GAP"
