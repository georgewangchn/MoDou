"""Decision-feedback channel tests (MoDou → MOSS, modou/feedback@1)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from asr.assurance import Finding
from asr.assurance.decision_feedback import (
    DECISION_FINDING_CODES,
    FEEDBACK_SCHEMA,
    build_feedback,
    feedback_from_assessment_dict,
    load_calibration,
    route_findings,
    write_feedback,
)


def test_routing_splits_decision_from_execution():
    findings = [
        Finding(code="BASELINE_DEFICIENT", message="vague", severity="HIGH"),
        Finding(code="SPEC_INCOMPLETE", message="no goal", severity="MEDIUM"),
        Finding(code="TARGET_MISS", message="no e2e", severity="HIGH"),
        Finding(code="EVIDENCE_GAP", message="exec-layer", severity="MEDIUM"),
        Finding(code="SEMANTIC_DRIFT", message="exec-layer", severity="LOW"),
    ]
    to_decision, to_execution = route_findings(findings)
    assert {f.code for f in to_decision} == DECISION_FINDING_CODES
    assert {f.code for f in to_execution} == {"EVIDENCE_GAP", "SEMANTIC_DRIFT"}


def test_envelope_schema_and_provenance(tmp_path):
    env = build_feedback(
        assessment_id="ASM-1", subject="proj", status="DEVIATION",
        findings=[Finding(code="EVIDENCE_GAP", message="x", severity="MEDIUM")],
        decision_ref="MOSS-DEC-1@v2")
    d = env.to_dict()
    assert d["schema_version"] == FEEDBACK_SCHEMA
    assert d["decision_ref"] == "MOSS-DEC-1@v2"
    assert d["deliverable_accepted"] == "DEVIATION"
    assert d["decision_findings"] == []          # exec-only findings filtered
    assert d["producer"]["runtime"] == "modou"
    assert d["producer"]["schema"] == FEEDBACK_SCHEMA
    p = write_feedback(env, tmp_path / "fb")
    loaded = json.loads(p.read_text())
    assert loaded["feedback_id"].startswith("FB-")
    assert loaded["feedback_id"] == env.feedback_id


def test_confidence_honest_when_no_ledger(tmp_path):
    c = load_calibration(None)
    assert c == {"calibration": None, "pm_precision": None, "pm_n": 0}
    c2 = load_calibration(tmp_path / "nonexistent.jsonl")
    assert c2["pm_n"] == 0 and c2["pm_precision"] is None


def test_confidence_from_ledger(tmp_path):
    from asr.assurance.calibration import QualityLedger
    led = QualityLedger(tmp_path / "q.jsonl")
    for _ in range(25):
        led.mark("fp-x", "tp", "pm")
    c = load_calibration(tmp_path / "q.jsonl")
    assert c["pm_n"] == 25 and c["pm_precision"] == 1.0 and c["calibration"] == "CALIBRATED"


def test_mode_a_assessment_carries_decision_ref(tmp_path):
    """spec YAML 的 decision_ref 一路贯穿到 Assessment 与反馈信封。"""
    import asr
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "goal: demo\nacceptance_criteria:\n  - add works\ndecision_ref: MOSS-DEC-42@v7\n")
    (tmp_path / "DESIGN.md").write_text("# D\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_add.py").write_text("def test_add():\n    assert 1\n")
    stdout = "tests/test_add.py::test_add PASSED [100%]\n1 passed\n"

    class FakeProc:
        returncode, stderr = 0, ""

        @property
        def stdout(self):
            return stdout

    with patch("asr.assurance.project.subprocess.run", return_value=FakeProc()):
        result = asyncio.run(asr.assess(tmp_path, spec, use_analyzer=False))
    assert result.decision_ref == "MOSS-DEC-42@v7"
    d = result.to_dict()
    assert d["decision_ref"] == "MOSS-DEC-42@v7" and d["assessment_id"].startswith("ASM-")
    env = feedback_from_assessment_dict(d)
    assert env.decision_ref == "MOSS-DEC-42@v7"
    assert env.acceptance_counts == {"evidenced": 1, "gaps": 0}
    assert env.deliverable_accepted == "PASS"


def test_mode_a_baseline_findings_reach_decision_channel(tmp_path):
    d = {
        "assessment_id": "ASM-9", "project": "p", "status": "INCONCLUSIVE",
        "created_at": "t",
        "findings": [
            {"code": "SPEC_INCOMPLETE", "severity": "MEDIUM", "message": "goal is required"},
            {"code": "EVIDENCE_GAP", "severity": "HIGH", "message": "no tests"},
        ],
        "acceptance": [],
    }
    env = feedback_from_assessment_dict(d, decision_ref="DEC@v1")
    codes = [f["code"] for f in env.decision_findings]
    assert codes == ["SPEC_INCOMPLETE"]     # EVIDENCE_GAP stays on the execution side
    assert env.deliverable_accepted == "INCONCLUSIVE"


def test_mode_c_pipeline_writes_envelopes(tmp_path):
    from asr.assurance.pipeline import run_board_assessment
    from asr.assurance.store import AssessmentStore
    from tests.board_fixture import build_board
    board = build_board(tmp_path / "b")
    store = AssessmentStore(tmp_path / "s")
    fb = tmp_path / "fb"
    report = asyncio.run(run_board_assessment(
        board_dir=board, no_llm=True, store=store,
        decision_ref="MOSS-DEC-C@v1", feedback_dir=fb))
    files = list(fb.glob("FB-*.json"))
    assert len(files) == len(report.assessments) >= 1
    for f in files:
        d = json.loads(f.read_text())
        assert d["schema_version"] == FEEDBACK_SCHEMA
        assert d["decision_ref"] == "MOSS-DEC-C@v1"
        assert d["deliverable_accepted"] == "INCONCLUSIVE"  # plumbing mode
        assert d["assessment_id"].startswith("ASM-")


def test_feedback_export_cli(tmp_path):
    from click.testing import CliRunner

    from asr.cli.main import cli
    asm = tmp_path / "asm.json"
    asm.write_text(json.dumps({
        "assessment_id": "ASM-CLI", "subject": "layered", "status": "DEVIATION",
        "kind": "design", "reassessment_of": "ASM-OLD",
        "findings": [{"code": "TARGET_MISS", "severity": "high",
                      "message": "no integration evidence", "fingerprint": "fp1"}],
    }))
    out = tmp_path / "fb"
    r = CliRunner().invoke(cli, ["feedback-export", "--assessment", str(asm),
                                 "--out-dir", str(out),
                                 "--decision-ref", "DEC-9@v2",
                                 "--ledger", str(tmp_path / "none.jsonl")],
                           catch_exceptions=False)
    assert r.exit_code == 0
    files = list(out.glob("FB-*.json"))
    assert len(files) == 1
    d = json.loads(files[0].read_text())
    assert d["decision_ref"] == "DEC-9@v2"
    assert d["reassessment_of"] == "ASM-OLD"     # MOSS updates in place
    assert [f["code"] for f in d["decision_findings"]] == ["TARGET_MISS"]
    assert d["confidence"]["pm_n"] == 0
