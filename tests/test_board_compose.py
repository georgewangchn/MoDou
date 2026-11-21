"""Tests for aggregation semantics + gates (Final-1.0 §5.4 table, §6.2)."""

from asr.assurance.compose import (
    AssessorVerdict,
    Finding,
    GateConfig,
    apply_gates,
    compose_assessment,
    compose_assurance,
    compose_status,
    make_fingerprint,
)

PAGES = {"tasks/T-101", "baseline/layered/design.md", "evidence/T-101/1",
         "seam/contracts", "context"}


def F(code="EVIDENCE_GAP", sev="high", conf=0.9, refs=None, subject=None):
    return Finding(code=code, severity=sev, confidence=conf,
                   evidence_refs=refs or ["tasks/T-101"],
                   subject=subject or {"work_item": "T-101", "revision": 3})


# ── status table ────────────────────────────────────────────────────────────
def test_all_clear_pass():
    assert compose_status(AssessorVerdict(all_clear=True)) == "PASS"


def test_findings_deviation():
    assert compose_status(AssessorVerdict(findings=[F()])) == "DEVIATION"


def test_insufficient_alone_is_inconclusive():
    assert compose_status(AssessorVerdict(insufficient_evidence=True)) == "INCONCLUSIVE"


def test_confirmed_findings_outrank_insufficient_selfreport():
    # gated findings are confirmed deviations even when other faces were
    # unverifiable (e.g. git_commit evidence without repo mapping)
    v = AssessorVerdict(insufficient_evidence=True, findings=[F()])
    assert compose_status(v) == "DEVIATION"


def test_baseline_deficient_deviation():
    assert compose_status(AssessorVerdict(baseline_quality="deficient")) == "DEVIATION"


def test_coverage_gap_beats_item_pass():
    v = AssessorVerdict(all_clear=True, obligations=4, covered=3)
    assert compose_status(v) == "DEVIATION"


def test_seam_unproven_is_inconclusive_not_pass():
    v = AssessorVerdict(all_clear=True, obligations=4, covered=4, seam="unproven")
    assert compose_status(v) == "INCONCLUSIVE"


def test_seam_gap_is_deviation():
    v = AssessorVerdict(all_clear=True, obligations=4, covered=4, seam="gap")
    assert compose_status(v) == "DEVIATION"


def test_full_proven_pass():
    v = AssessorVerdict(all_clear=True, obligations=4, covered=4,
                        seam="proven", outcome="proven")
    assert compose_status(v) == "PASS"


def test_outcome_unproven_inconclusive():
    v = AssessorVerdict(all_clear=True, obligations=4, covered=4,
                        seam="proven", outcome="unproven")
    assert compose_status(v) == "INCONCLUSIVE"


# ── assurance dims ──────────────────────────────────────────────────────────
def test_dims_insufficient():
    d = compose_assurance(AssessorVerdict(insufficient_evidence=True), "design")
    assert set(d.values()) == {"INCONCLUSIVE"}


def test_dims_by_code():
    d = compose_assurance(
        AssessorVerdict(findings=[F("CONSTRAINT_VIOLATION"), F("TARGET_MISS")]),
        "design")
    assert d["governance"] == "DEVIATION"
    assert d["outcome"] == "DEVIATION"
    assert d["deliverable"] == "PASS"
    assert d["execution"] == "UNASSESSED"


def test_dims_outcome_proven():
    d = compose_assurance(AssessorVerdict(all_clear=True, outcome="proven"), "design")
    assert d["outcome"] == "PASS"


# ── gates ───────────────────────────────────────────────────────────────────
def test_gate_citation_drops():
    rep = apply_gates([F(refs=["tasks/NOPE"])], PAGES, GateConfig())
    assert not rep.kept and rep.dropped[0][1].startswith("citation")


def test_gate_confidence_floor():
    rep = apply_gates([F(conf=0.3)], PAGES, GateConfig(confidence_floor=0.6))
    assert not rep.kept and "floor" in rep.dropped[0][1]


def test_gate_budget_topn():
    fs = [F(conf=0.7 + i * 0.01, refs=[f"evidence/T-101/{i}"]) for i in range(1, 6)]
    pages = PAGES | {f"evidence/T-101/{i}" for i in range(1, 6)}
    rep = apply_gates(fs, pages, GateConfig(budget=3))
    assert len(rep.kept) == 3 and len(rep.dropped) == 2


def test_gate_invalid_code():
    rep = apply_gates([F(code="WAT")], PAGES, GateConfig())
    assert rep.dropped[0][1].startswith("invalid")


def test_gate_severity_order():
    rep = apply_gates([F(sev="low"), F(sev="critical")], PAGES, GateConfig(budget=1))
    assert rep.kept[0].severity == "critical"


# ── fingerprint + assessment assembly ───────────────────────────────────────
def test_fingerprint_stable_and_sensitive():
    a = make_fingerprint("T-101", "EVIDENCE_GAP", "rev1", "rev1")
    b = make_fingerprint("T-101", "EVIDENCE_GAP", "rev1", "rev1")
    c = make_fingerprint("T-101", "EVIDENCE_GAP", "rev2", "rev1")
    assert a == b and a != c and a.startswith("sha256:")


def test_compose_assessment_end_to_end():
    v = AssessorVerdict(findings=[F()], obligations=4, covered=4, outcome="proven")
    gates = apply_gates(v.findings, PAGES, GateConfig())
    a = compose_assessment(v, "design", "layered",
                           scope={"board": "b", "as_of_commit": "r"},
                           baseline_refs=[{"type": "design", "source": "designs/layered/design.md"}],
                           gates=gates)
    assert a.status == "DEVIATION"
    d = a.to_dict()
    assert d["schema_version"] == "modou/assessment@1.0"
    assert d["findings"][0]["code"] == "EVIDENCE_GAP"
    assert a.summary_line().startswith(a.assessment_id)
