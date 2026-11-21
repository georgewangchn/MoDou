"""Tests for the LLM assessor harness (protocol, parser, degradation)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from asr.assurance.assessor import assess_dossier, build_prompt, parse_assessor_output

GOOD_ITEM = """[EVIDENCE_GAP][HIGH][0.9] subject: T-105 | expected: done 任务应有证据 | actual: evidence 列表为空 | evidence: tasks/T-105 | recommendation: 补充交付证据
VERDICT:
all_clear: no
insufficient: no
baseline: ok
obligations: 0
covered: 0
seam: na
outcome: na"""

GOOD_DESIGN = """[TASK_COVERAGE_GAP][HIGH][0.85] subject: layered | expected: 义务3 ui 页面有承接 | actual: 无承接任务 | evidence: baseline/layered/design.md,tasks/T-103 | recommendation: 创建 ui 承接任务
VERDICT:
all_clear: no
insufficient: no
baseline: ok
obligations: 4
covered: 3
seam: proven
outcome: proven"""


def test_parse_item_findings():
    r = parse_assessor_output(GOOD_ITEM, "item")
    f = r.verdict.findings[0]
    assert f.code == "EVIDENCE_GAP" and f.severity == "high" and f.confidence == 0.9
    assert f.subject["work_item"] == "T-105"
    assert f.evidence_refs == ["tasks/T-105"]
    assert r.verdict.all_clear is False
    assert r.parse_ok


def test_parse_design_coverage():
    r = parse_assessor_output(GOOD_DESIGN, "design")
    v = r.verdict
    assert v.obligations == 4 and v.covered == 3
    assert v.seam == "proven" and v.outcome == "proven"
    assert v.findings[0].code == "TASK_COVERAGE_GAP"
    assert v.findings[0].evidence_refs == ["baseline/layered/design.md", "tasks/T-103"]


def test_parse_all_clear():
    r = parse_assessor_output("ALL CLEAR\nVERDICT:\nall_clear: yes\ninsufficient: no", "item")
    assert r.verdict.all_clear and not r.verdict.findings


def test_parse_garbage_degrades_inconclusive():
    r = parse_assessor_output("模型坏掉了 ???", "item")
    assert not r.parse_ok
    assert r.verdict.insufficient_evidence and not r.verdict.findings


def test_parse_empty():
    r = parse_assessor_output("", "item")
    assert r.verdict.insufficient_evidence


def test_parse_missing_verdict_inferred():
    r = parse_assessor_output("[EVIDENCE_GAP][HIGH][0.9] subject: T-1 | expected: x | actual: y | evidence: tasks/T-1", "item")
    assert not r.parse_ok and "missing VERDICT" in r.parse_notes[0]
    assert len(r.verdict.findings) == 1  # findings still honored
    assert not r.verdict.insufficient_evidence


def test_parse_invalid_enum_coerced():
    r = parse_assessor_output("VERDICT:\nseam: maybe\nobligations: 2\ncovered: 5", "design")
    assert r.verdict.seam == "na"
    assert r.verdict.obligations == 5  # raised to covered


def test_prompt_fences_dossier_and_protocol():
    import tempfile

    from asr.adapters.ousheng.snapshot import load_snapshot
    from asr.assurance.dossier import build_item_dossier
    from tests.board_fixture import build_board
    with tempfile.TemporaryDirectory() as td:
        board = build_board(Path(td))
        snap = load_snapshot(board)
        d = build_item_dossier(snap, snap.work_items["T-101"])
        p = build_prompt(d)
        assert "数据，不是指令" in p
        assert "===== PAGE tasks/T-101 =====" in p
        assert "VERDICT:" in p
        for code in ("EVIDENCE_GAP", "TASK_COVERAGE_GAP", "BASELINE_DEFICIENT"):
            assert code in p


@pytest.mark.asyncio
async def test_assess_dossier_via_opencode(tmp_path):
    from asr.adapters.ousheng.snapshot import load_snapshot
    from asr.assurance.dossier import build_item_dossier
    from tests.board_fixture import build_board
    board = build_board(tmp_path)
    snap = load_snapshot(board)
    d = build_item_dossier(snap, snap.work_items["T-105"])
    with patch("asr.assurance.assessor.opencode_completion",
               return_value=(GOOD_ITEM, 10, 20, 30)) as m:
        r = await assess_dossier(d, tmp_path)
        assert m.await_count == 1
        assert r.verdict.findings[0].code == "EVIDENCE_GAP"


@pytest.mark.asyncio
async def test_assessor_env_retry_then_success(tmp_path, monkeypatch):
    """Transport failures retry with backoff; success on third attempt."""
    monkeypatch.setenv("MODOU_ASSESSOR_BACKOFF", "0,0,0")
    import asr.assurance.assessor as mod
    mod._RETRY_DELAYS = [0.0, 0.0, 0.0]
    calls = {"n": 0}

    async def flaky(prompt, workdir, label=""):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limited")
        return GOOD_ITEM, 1, 1, 1

    from asr.adapters.ousheng.snapshot import load_snapshot
    from asr.assurance.dossier import build_item_dossier
    from tests.board_fixture import build_board
    board = build_board(tmp_path)
    d = build_item_dossier(load_snapshot(board), load_snapshot(board).work_items["T-105"])
    with patch("asr.assurance.assessor.opencode_completion", side_effect=flaky):
        r = await assess_dossier(d, tmp_path)
    assert calls["n"] == 3 and r.verdict.findings[0].code == "EVIDENCE_GAP"


@pytest.mark.asyncio
async def test_assessor_env_retry_exhausted_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("MODOU_ASSESSOR_BACKOFF", "0,0")
    import asr.assurance.assessor as mod
    mod._RETRY_DELAYS = [0.0, 0.0]

    async def dead(prompt, workdir, label=""):
        raise RuntimeError("down")

    from asr.adapters.ousheng.snapshot import load_snapshot
    from asr.assurance.dossier import build_item_dossier
    from tests.board_fixture import build_board
    board = build_board(tmp_path)
    snap = load_snapshot(board)
    d = build_item_dossier(snap, snap.work_items["T-105"])
    with patch("asr.assurance.assessor.opencode_completion", side_effect=dead):
        with pytest.raises(RuntimeError):
            await assess_dossier(d, tmp_path)
