"""P4 watch/trend tests: cycle isolation, recurrence detection, aggregation."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asr.assurance.store import AssessmentStore
from asr.assurance.watch import compute_trend, run_cycle
from asr.cli.main import cli
from tests.board_fixture import build_board

ITEM_OUT = ("[EVIDENCE_GAP][HIGH][0.9] subject: BUG-201 | expected: e | actual: a "
            "| evidence: tasks/BUG-201 | recommendation: r\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na\n")
DESIGN_OUT = ("VERDICT:\nall_clear: yes\ninsufficient: no\nbaseline: ok\n"
              "obligations: 4\ncovered: 4\nseam: proven\noutcome: proven\n")


async def _fake(dossier, workdir):
    from asr.assurance.assessor import parse_assessor_output
    text = DESIGN_OUT if dossier.kind == "design" else ITEM_OUT
    return parse_assessor_output(text, dossier.kind)


@pytest.fixture()
def board(tmp_path):
    return build_board(tmp_path)


def test_cycle_writes_trend_and_last_run(board, tmp_path):
    store = AssessmentStore(tmp_path / "store")
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake):
        s = asyncio.run(run_cycle(1, board, store, fetch=False, sample_rate=1.0))
    assert s.rev == "HEAD" and not s.errors
    assert s.statuses["layered"] == "PASS"
    assert s.statuses["BUG-201"] == "DEVIATION"
    assert len(s.new_fingerprints) == 1  # the EVIDENCE_GAP finding
    trend = (tmp_path / "store/trend.jsonl").read_text().splitlines()
    assert len(trend) == 1 and json.loads(trend[0])["cycle"] == 1
    last = json.loads((tmp_path / "store/last-run.json").read_text())
    assert last["statuses"]["BUG-201"] == "DEVIATION"


def test_second_cycle_detects_recurrence(board, tmp_path):
    store = AssessmentStore(tmp_path / "store")
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake):
        s1 = asyncio.run(run_cycle(1, board, store, fetch=False, sample_rate=1.0))
        s2 = asyncio.run(run_cycle(2, board, store, fetch=False, sample_rate=1.0))
    assert s1.new_fingerprints and not s1.recurred_fingerprints
    assert not s2.new_fingerprints and s2.recurred_fingerprints == s1.new_fingerprints


def test_cycle_isolation_on_snapshot_error(tmp_path, board):
    from asr.adapters.ousheng.snapshot import SnapshotError
    store = AssessmentStore(tmp_path / "store")
    with patch("asr.assurance.watch.run_board_assessment",
               side_effect=SnapshotError("schema drift")):
        s = asyncio.run(run_cycle(1, board, store, fetch=False))
    assert s.errors and "schema drift" in s.errors[0]
    assert json.loads((tmp_path / "store/last-run.json").read_text())["cycle"] == 1


def test_trend_aggregation(board, tmp_path):
    store = AssessmentStore(tmp_path / "store")
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake):
        asyncio.run(run_cycle(1, board, store, fetch=False, sample_rate=1.0))
        asyncio.run(run_cycle(2, board, store, fetch=False, sample_rate=1.0))
    t = compute_trend(store)
    assert t["runs"] == 4  # 2 cycles x (design+sampled item)
    assert t["subjects"] >= 2
    assert t["status_histogram_latest"].get("DEVIATION") >= 1
    assert t["code_histogram_latest"].get("EVIDENCE_GAP") >= 1
    assert len(t["recurred_fingerprints"]) == 1
    assert t["resolved_fingerprints"] == []
    assert "latest assessment per subject" in t["denominators"]["subjects"]


def test_trend_resolved_when_finding_disappears(board, tmp_path):
    store = AssessmentStore(tmp_path / "store")

    async def _healed(dossier, workdir):
        from asr.assurance.assessor import parse_assessor_output
        text = DESIGN_OUT if dossier.kind == "design" else (
            "ALL CLEAR\nVERDICT:\nall_clear: yes\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na\n")
        return parse_assessor_output(text, dossier.kind)

    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake):
        asyncio.run(run_cycle(1, board, store, fetch=False, sample_rate=1.0))
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_healed):
        asyncio.run(run_cycle(2, board, store, fetch=False, sample_rate=1.0))
    t = compute_trend(store)
    assert t["resolved_fingerprints"]  # was present, now gone
    assert t["status_histogram_latest"].get("DEVIATION") is None


def test_cli_watch_once_and_trend(board, tmp_path):
    runner = CliRunner()
    store = str(tmp_path / "store")
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake):
        r = runner.invoke(cli, ["board-watch", "--board", str(board),
                                "--store-dir", store, "--once", "--no-fetch",
                                "--sample-rate", "1.0"],
                          catch_exceptions=False)
    assert r.exit_code == 1  # DEVIATION present, env healthy
    assert "cycle 1" in r.output and "errors=0" in r.output

    r2 = runner.invoke(cli, ["board-trend", "--store-dir", store,
                             "--json-output", str(tmp_path / "t.json")],
                       catch_exceptions=False)
    assert "Recurred: 0" in r2.output
    assert json.loads((tmp_path / "t.json").read_text())["runs"] == 2  # design + sampled item
