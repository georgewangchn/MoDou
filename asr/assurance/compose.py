"""Aggregation semantics + quality gates + Assessment composition.

Final-1.0 §5.4 aggregation table and §6.2 four gates, encoded as pure
functions (fully unit-testable). All LLM output arrives as candidate
findings + a structured verdict; this module is the deterministic half.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── Deviation codes (Final-1.0 §7.2) ────────────────────────────────────────
DEVIATION_CODES = {
    "OMISSION", "SEMANTIC_DRIFT", "CONSTRAINT_VIOLATION", "TASK_COVERAGE_GAP",
    "DEPENDENCY_VIOLATION", "EXECUTION_DEVIATION", "DEADLINE_BREACH",
    "RESOURCE_OVERRUN", "QUALITY_FAILURE", "EVIDENCE_GAP", "TARGET_MISS",
    "FALSE_COMPLETION", "AUTHORIZATION_GAP", "BASELINE_DEFICIENT",
    "INCONCLUSIVE",
}

# code → assurance dimension
CODE_DIM = {
    "CONSTRAINT_VIOLATION": "governance", "AUTHORIZATION_GAP": "governance",
    "DEADLINE_BREACH": "execution", "EXECUTION_DEVIATION": "execution",
    "DEPENDENCY_VIOLATION": "execution", "RESOURCE_OVERRUN": "execution",
    "EVIDENCE_GAP": "deliverable", "QUALITY_FAILURE": "deliverable",
    "FALSE_COMPLETION": "deliverable", "TASK_COVERAGE_GAP": "deliverable",
    "SEMANTIC_DRIFT": "deliverable", "OMISSION": "deliverable",
    "BASELINE_DEFICIENT": "deliverable",
    "TARGET_MISS": "outcome",
}

VALID_SEVERITIES = {"critical", "high", "medium", "low"}


def make_fingerprint(subject_id: str, code: str, baseline_rev: str,
                     evidence_rev: str) -> str:
    h = hashlib.sha256()
    for part in (subject_id, code, baseline_rev, evidence_rev):
        h.update((part or "").encode())
        h.update(b"\x1f")
    return f"sha256:{h.hexdigest()[:16]}"


@dataclass
class Finding:
    code: str
    severity: str = "medium"            # critical|high|medium|low
    confidence: float = 0.5
    subject: dict = field(default_factory=dict)   # {work_item, revision, design?}
    expected: str = ""
    actual: str = ""
    evidence_refs: list[str] = field(default_factory=list)  # dossier page ids
    recommendation: str = ""
    rationale: str = ""
    fingerprint: str = ""

    def validate(self) -> list[str]:
        errs = []
        if self.code not in DEVIATION_CODES:
            errs.append(f"unknown code {self.code}")
        if self.severity not in VALID_SEVERITIES:
            errs.append(f"bad severity {self.severity}")
        if not 0.0 <= self.confidence <= 1.0:
            errs.append(f"bad confidence {self.confidence}")
        if not self.evidence_refs:
            errs.append("no evidence_refs (citation gate will drop)")
        return errs


# ── Gates (Final-1.0 §6.2) ───────────────────────────────────────────────────
@dataclass
class GateConfig:
    confidence_floor: float = 0.6
    budget: int = 20


@dataclass
class GateReport:
    kept: list[Finding] = field(default_factory=list)
    dropped: list[tuple[Finding, str]] = field(default_factory=list)


def apply_gates(findings: list[Finding], dossier_page_ids: set[str],
                cfg: GateConfig) -> GateReport:
    """Citation gate → confidence gate → budget gate (deterministic)."""
    rep = GateReport()
    valid: list[Finding] = []
    for f in findings:
        if errs := f.validate():
            rep.dropped.append((f, "invalid: " + "; ".join(errs)))
            continue
        # citation gate: every evidence ref must be a dossier page id or item id
        bad = [r for r in f.evidence_refs
               if r not in dossier_page_ids
               and not any(r == pid or r.startswith(f"tasks/{r}")
                           for pid in dossier_page_ids)]
        if bad:
            rep.dropped.append((f, f"citation unresolved: {bad}"))
            continue
        if f.confidence < cfg.confidence_floor:
            rep.dropped.append((f, f"confidence {f.confidence} < floor {cfg.confidence_floor}"))
            continue
        valid.append(f)

    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    valid.sort(key=lambda f: (sev_rank.get(f.severity, 9), -f.confidence))
    if len(valid) > cfg.budget:
        rep.kept = valid[:cfg.budget]
        for f in valid[cfg.budget:]:
            rep.dropped.append((f, "over budget"))
    else:
        rep.kept = valid
    return rep


# ── Assessor verdict (parsed from the LLM protocol output) ──────────────────
@dataclass
class AssessorVerdict:
    all_clear: bool = False
    insufficient_evidence: bool = False   # could not evaluate → INCONCLUSIVE
    baseline_quality: str = "ok"          # ok | deficient
    obligations: int = 0
    covered: int = 0
    seam: str = "na"                      # na | proven | gap | unproven
    outcome: str = "na"                   # na | proven | unproven
    findings: list[Finding] = field(default_factory=list)


# ── Aggregation semantics (Final-1.0 §5.4 table, code + tests) ──────────────
def compose_status(v: AssessorVerdict) -> str:
    """Item level: findings→DEVIATION; clear→PASS; insufficient→INCONCLUSIVE.

    Design level adds: coverage gap→DEVIATION; coverage complete but seam
    unproven→INCONCLUSIVE (never PASS on unproven seams); baseline
    deficient→DEVIATION (BASELINE_DEFICIENT is a high-value finding).

    Ordering rationale: gated findings are *confirmed* deviations (they
    passed citation + confidence gates), so they outrank a global
    "insufficient evidence" self-report — dropping confirmed findings
    because other faces were unverifiable would understate the audit.
    Unverifiable faces still surface via assurance dims and the verdict.
    """
    if v.findings:
        return "DEVIATION"
    if v.baseline_quality == "deficient":
        return "DEVIATION"
    if v.insufficient_evidence:
        return "INCONCLUSIVE"
    if v.seam == "gap":
        return "DEVIATION"
    if v.seam == "unproven":
        return "INCONCLUSIVE"
    if v.obligations > 0 and v.covered < v.obligations:
        return "DEVIATION"        # coverage gap even with every item PASS
    if v.outcome == "unproven":
        return "INCONCLUSIVE"
    return "PASS"


def compose_assurance(v: AssessorVerdict, kind: str) -> dict:
    """governance/execution/deliverable/outcome dims; UNASSESSED default."""
    dims = {"governance": "UNASSESSED", "execution": "UNASSESSED",
            "deliverable": "UNASSESSED", "outcome": "UNASSESSED"}
    if v.insufficient_evidence:
        return {k: "INCONCLUSIVE" for k in dims}

    flagged = {CODE_DIM.get(f.code) for f in v.findings if f.code in CODE_DIM}
    # an item/design dossier always assesses deliverable evidence face
    dims["deliverable"] = "DEVIATION" if "deliverable" in flagged else "PASS"
    dims["governance"] = "DEVIATION" if "governance" in flagged else (
        "PASS" if v.findings or v.all_clear else "UNASSESSED")
    dims["execution"] = "DEVIATION" if "execution" in flagged else (
        "PASS" if v.all_clear else "UNASSESSED")
    if kind == "design":
        dims["outcome"] = "DEVIATION" if "outcome" in flagged else (
            "INCONCLUSIVE" if v.outcome == "unproven" else
            "PASS" if v.outcome == "proven" else "UNASSESSED")
    return dims


# ── Assessment (modou/assessment@1.0) ────────────────────────────────────────
SCHEMA_VERSION = "modou/assessment@1.0"


def new_assessment_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"ASM-{stamp}"


@dataclass
class Assessment:
    assessment_id: str
    kind: str                          # design | item
    subject: str
    scope: dict = field(default_factory=dict)
    baseline_refs: list[dict] = field(default_factory=list)
    assurance: dict = field(default_factory=dict)
    status: str = "INCONCLUSIVE"
    findings: list[Finding] = field(default_factory=list)
    dropped_findings: list[dict] = field(default_factory=list)
    reassessment_of: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "assessment_id": self.assessment_id,
            "kind": self.kind,
            "subject": self.subject,
            "scope": self.scope,
            "baseline_refs": self.baseline_refs,
            "assurance": self.assurance,
            "status": self.status,
            "findings": [f.__dict__ for f in self.findings],
            "dropped_findings": self.dropped_findings,
            "reassessment_of": self.reassessment_of,
            "created_at": self.created_at,
        }

    def summary_line(self) -> str:
        codes = [f.code for f in self.findings]
        return (f"{self.assessment_id} [{self.kind}:{self.subject}] {self.status}"
                + (f" findings={codes}" if codes else ""))


def compose_assessment(verdict: AssessorVerdict, kind: str, subject: str,
                       scope: dict, baseline_refs: list[dict],
                       gates: GateReport,
                       reassessment_of: str | None = None) -> Assessment:
    a = Assessment(assessment_id=new_assessment_id(), kind=kind, subject=subject,
                   scope=scope, baseline_refs=baseline_refs,
                   reassessment_of=reassessment_of)
    a.findings = gates.kept
    a.dropped_findings = [{"code": f.code, "reason": r} for f, r in gates.dropped]
    a.status = compose_status(verdict)
    a.assurance = compose_assurance(verdict, kind)
    return a
