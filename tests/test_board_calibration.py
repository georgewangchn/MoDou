"""Calibration tests: suite truth, metrics, thresholds, ledger, CLI runner."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asr.assurance.calibration import (
    QualityLedger,
    Thresholds,
    build_calibration_suite,
    evaluate,
)
from asr.cli.main import cli

# ── fake assessor keyed on dossier.subject (CI only; real runs use the LLM) ──
ITEM_OUTPUT = {
    "T-A": ("ALL CLEAR\nVERDICT:\nall_clear: yes\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-B": ("[EVIDENCE_GAP][HIGH][0.9] subject: T-B | expected: done 应有证据 | actual: evidence 空 "
            "| evidence: tasks/T-B | recommendation: 补证据\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-C": ("[FALSE_COMPLETION][HIGH][0.9] subject: T-C | expected: locator 可解析 | actual: 悬空 "
            "| evidence: tasks/T-C | recommendation: 修 locator\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-D": ("[TARGET_MISS][HIGH][0.9] subject: T-D | expected: 证据提及 v9.9 | actual: 未提及 "
            "| evidence: tasks/T-D | recommendation: 补版本证据\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-E": ("[DEADLINE_BREACH][MEDIUM][0.9] subject: T-E | expected: due 2020 前完成 | actual: 仍 doing "
            "| evidence: tasks/T-E | recommendation: 排期\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-F": ("VERDICT:\nall_clear: no\ninsufficient: yes\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
    "T-G": ("[AUTHORIZATION_GAP][HIGH][0.9] subject: T-G | expected: breaking 需人审 | actual: 无 ack "
            "| evidence: tasks/T-G | recommendation: 补审\n"
            "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
            "obligations: 0\ncovered: 0\nseam: na\noutcome: na"),
}
DESIGN_OUTPUT = (
    "[TASK_COVERAGE_GAP][HIGH][0.9] subject: calib | expected: 义务4 有承接 | actual: 无任务 "
    "| evidence: baseline/calib/design.md | recommendation: 开单\n"
    "VERDICT:\nall_clear: no\ninsufficient: no\nbaseline: ok\n"
    "obligations: 4\ncovered: 3\nseam: na\noutcome: na"
)


async def _fake_assessor(dossier, workdir):
    from asr.assurance.assessor import parse_assessor_output
    text = DESIGN_OUTPUT if dossier.kind == "design" else ITEM_OUTPUT[dossier.subject]
    return parse_assessor_output(text, dossier.kind)


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    return build_calibration_suite(tmp_path_factory.mktemp("calib"))


def test_suite_truth_consistent(suite: Path):
    truth = json.loads((suite / "expected.json").read_text())
    ids = [c["id"] for c in truth["cases"]]
    assert len(ids) == len(set(ids)) == 8
    # every item case exists on the board
    for c in truth["cases"]:
        if c.get("kind", "item") == "item":
            assert (suite / f"board/.ousheng/work/{c['id']}.yaml").exists()
    assert (suite / "board/.ousheng/designs/calib/design.md").exists()


def test_evaluate_metrics_perfect():
    report = {"assessments": [
        {"subject": "T-A", "findings": []},
        {"subject": "T-B", "findings": [{"code": "EVIDENCE_GAP"}]},
        {"subject": "T-C", "findings": [{"code": "FALSE_COMPLETION"}]},
        {"subject": "T-D", "findings": [{"code": "TARGET_MISS"}]},
        {"subject": "T-E", "findings": [{"code": "DEADLINE_BREACH"}]},
        {"subject": "T-F", "findings": [], "status": "INCONCLUSIVE"},
        {"subject": "T-G", "findings": [{"code": "AUTHORIZATION_GAP"}]},
        {"subject": "calib", "findings": [{"code": "TASK_COVERAGE_GAP"}]},
    ]}
    m = evaluate(report, {"cases": [
        {"id": "T-A", "expect": "CLEAN"},
        {"id": "T-B", "expect": ["EVIDENCE_GAP"]},
        {"id": "T-F", "expect": "INCONCLUSIVE"},
        {"id": "calib", "kind": "design", "subject": "calib", "expect": ["TASK_COVERAGE_GAP"]},
    ]})
    assert m.detection_rate() == 1.0
    assert not m.false_positives and not m.inconclusive_violations
    assert Thresholds().check(m) and all(c["pass"] for c in Thresholds().check(m))


def test_evaluate_misses_and_fp():
    report = {"assessments": [
        {"subject": "T-A", "findings": [{"code": "SEMANTIC_DRIFT"}]},   # FP on normal
        {"subject": "T-B", "findings": []},                              # miss
        {"subject": "T-F", "findings": [], "status": "DEVIATION"},       # discipline violation
    ]}
    m = evaluate(report, {"cases": [
        {"id": "T-A", "expect": "CLEAN"},
        {"id": "T-B", "expect": ["EVIDENCE_GAP"]},
        {"id": "T-F", "expect": "INCONCLUSIVE"},
    ]})
    assert m.detection_rate() == 0.0 and m.expected_misses
    assert m.false_positives == ["T-A:SEMANTIC_DRIFT"]
    assert m.inconclusive_violations == ["T-F:DEVIATION"]
    assert not all(c["pass"] for c in Thresholds().check(m))


def test_evaluate_inconclusive_alternatives():
    # strict-but-cited EVIDENCE_GAP on unverifiable evidence is disciplined
    m = evaluate({"assessments": [{"subject": "T-F", "status": "DEVIATION",
                                   "findings": [{"code": "EVIDENCE_GAP"}]}]},
                 {"cases": [{"id": "T-F", "expect": "INCONCLUSIVE",
                             "accept_alternatives": ["EVIDENCE_GAP"]}]})
    assert m.inconclusive_ok and not m.inconclusive_violations
    # any other deviation without the alternative is a violation
    m2 = evaluate({"assessments": [{"subject": "T-F", "status": "DEVIATION",
                                    "findings": [{"code": "FALSE_COMPLETION"}]}]},
                  {"cases": [{"id": "T-F", "expect": "INCONCLUSIVE",
                              "accept_alternatives": ["EVIDENCE_GAP"]}]})
    assert m2.inconclusive_violations


def test_ledger_precision(tmp_path):
    led = QualityLedger(tmp_path / "led.jsonl")
    assert led.precision() == (0, None)
    for i in range(15):
        led.mark(f"fp{i}", "tp", "pm")
    led.mark("fpX", "fp", "pm")
    n, p = led.precision()
    assert n == 16 and p == 15 / 16
    with pytest.raises(ValueError):
        led.mark("x", "banana", "pm")


def test_calibrate_cli_with_fake_assessor(suite, tmp_path):
    runner = CliRunner()
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake_assessor):
        r = runner.invoke(cli, [
            "board-calibrate", "--suite", str(suite),
            "--workdir", str(tmp_path / "stage"),
            "--store-dir", str(tmp_path / "store"),
            "--ledger", str(tmp_path / "led.jsonl"),
            "--json-output", str(tmp_path / "calib.json"),
        ], catch_exceptions=False)
    out = r.output
    assert "Detection rate: 100% (6/6)" in out
    assert "False positives on normals: 0/1" in out
    assert "[PASS] detection_rate" in out
    # precision pending (no marks) → overall NOT CALIBRATED, exit 1
    assert "NOT CALIBRATED" in out and r.exit_code == 1
    calib = json.loads((tmp_path / "calib.json").read_text())
    assert calib["calibrated"] is False
    pc = calib["precision_check"]
    assert pc["pass"] is False and pc["marks"] == 0
    assert calib["rounds"][0]["pass"] is True  # deterministic gates all green


def test_mark_cli(tmp_path, suite):
    import asyncio

    from asr.assurance.pipeline import run_board_assessment
    from asr.assurance.store import AssessmentStore

    store_dir = tmp_path / "store"
    store = AssessmentStore(store_dir)
    with patch("asr.assurance.pipeline.assess_dossier", side_effect=_fake_assessor):
        asyncio.run(run_board_assessment(
            suite / "board", items=["T-B"], workdir=tmp_path / "stage",
            store=store))
    asm_id = store.entries()[0]["assessment_id"]
    asm_file = store_dir / f"{asm_id}.json"

    runner = CliRunner()
    r = runner.invoke(cli, ["board-mark", "--assessment", str(asm_file),
                            "--finding-index", "0", "--verdict", "tp",
                            "--by", "pm", "--ledger", str(tmp_path / "led.jsonl")],
                      catch_exceptions=False)
    assert "Marked" in r.output and "precision=100%" in r.output
