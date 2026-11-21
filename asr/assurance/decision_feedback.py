"""Decision-feedback channel — MoDou → MOSS (pull-based file contract).

MOSS is the Decision Runtime: it owns goals, acceptance standards and
decision versioning. This module shapes what MoDou knows into a *decision-
facing* envelope (``modou/feedback@1``), written to a directory MOSS pulls
from. MoDou never pushes: no daemon, no callback — same posture as the
cruise exit-code contract and the PM's last-run.json signal.

Three feedback flows (see docs / CHANGELOG 3.2.0 for the full rationale):
① decision outcome  — deliverable accepted? (status + acceptance counts)
② baseline quality  — findings that belong to the *decision layer*
                      (BASELINE_DEFICIENT / SPEC_INCOMPLETE / TARGET_MISS),
                      routed away from execution-layer writeback
③ confidence        — MoDou's own calibration state, so MOSS can decide
                      between auto-adopt and human review

Execution-layer findings (EVIDENCE_GAP etc.) deliberately do NOT enter this
channel: they already have a destination (mode C MCP writeback, PM triage).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

FEEDBACK_SCHEMA = "modou/feedback@1"

# Findings whose root cause lives in the decision/specification layer —
# the signal MOSS needs to improve the decision itself instead of sending
# OuSheng into a pointless rework loop.
DECISION_FINDING_CODES = frozenset({
    "BASELINE_DEFICIENT",   # baseline (design/spec) is unacceptably vague
    "SPEC_INCOMPLETE",      # acceptance basis incomplete (mode A)
    "TARGET_MISS",          # no integration-level evidence for the target
})


def _get(f, key: str, default=""):
    """Read a field from either a Finding object or its dict form."""
    if isinstance(f, dict):
        return f.get(key, default)
    return getattr(f, key, default)


def route_findings(findings: list) -> tuple[list, list]:
    """Split findings into (to_decision, to_execution) by code."""
    to_decision = [f for f in findings if _get(f, "code") in DECISION_FINDING_CODES]
    to_execution = [f for f in findings if _get(f, "code") not in DECISION_FINDING_CODES]
    return to_decision, to_execution


@dataclass
class FeedbackEnvelope:
    """One decision-facing summary per assessment (MOSS's main diet)."""

    assessment_id: str
    subject: str
    deliverable_accepted: str                 # PASS / DEVIATION / INCONCLUSIVE / BLOCKED
    decision_ref: str | None = None           # opaque anchor, MOSS-owned format
    reassessment_of: str | None = None        # MOSS updates in place, not re-opens
    kind: str = "item"                        # item (mode A subject) / design / item(mode C)
    acceptance_counts: dict = field(default_factory=dict)
    decision_findings: list[dict] = field(default_factory=list)
    confidence: dict = field(default_factory=dict)
    feedback_id: str = ""
    produced_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        # Runtime import: this module sits on asr/__init__'s import chain —
        # a top-level `from asr import __version__` would be circular.
        from asr import __version__
        return {
            "schema_version": FEEDBACK_SCHEMA,
            "feedback_id": self.feedback_id,
            "decision_ref": self.decision_ref,
            "assessment_id": self.assessment_id,
            "reassessment_of": self.reassessment_of,
            "subject": self.subject,
            "kind": self.kind,
            "deliverable_accepted": self.deliverable_accepted,
            "acceptance_counts": self.acceptance_counts,
            "decision_findings": self.decision_findings,
            "confidence": self.confidence,
            "produced_at": self.produced_at,
            "producer": {"runtime": "modou", "version": __version__,
                         "schema": FEEDBACK_SCHEMA},
        }


def _new_feedback_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"FB-{stamp}"


def _finding_digest(f) -> dict:
    """Code/severity/message/fingerprint — everything MOSS acts on, nothing more."""
    return {
        "code": _get(f, "code"),
        "severity": _get(f, "severity"),
        "message": str(_get(f, "message"))[:300],
        "fingerprint": _get(f, "fingerprint", None),
    }


def load_calibration(ledger_path: Path | str | None) -> dict:
    """③ confidence from the PM spot-check ledger (mode C); null when unknown.

    Honest-by-construction: no ledger → no numbers, never a guessed value.
    """
    if not ledger_path:
        return {"calibration": None, "pm_precision": None, "pm_n": 0}
    from asr.assurance.calibration import QualityLedger
    n, p = QualityLedger(Path(ledger_path)).precision()
    return {"calibration": "CALIBRATED" if (p is not None and p >= 0.8 and n >= 20)
            else "SHADOW", "pm_precision": p, "pm_n": n}


def build_feedback(
    assessment_id: str,
    subject: str,
    status: str,
    findings: list,
    *,
    kind: str = "item",
    decision_ref: str | None = None,
    reassessment_of: str | None = None,
    acceptance_counts: dict | None = None,
    ledger_path: Path | str | None = None,
) -> FeedbackEnvelope:
    to_decision, _ = route_findings(findings)
    return FeedbackEnvelope(
        assessment_id=assessment_id,
        subject=subject,
        deliverable_accepted=status,
        decision_ref=decision_ref,
        reassessment_of=reassessment_of,
        kind=kind,
        acceptance_counts=acceptance_counts or {},
        decision_findings=[_finding_digest(f) for f in to_decision],
        confidence=load_calibration(ledger_path),
        feedback_id=_new_feedback_id(),
    )


def write_feedback(envelope: FeedbackEnvelope, feedback_dir: Path | str) -> Path:
    out_dir = Path(feedback_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{envelope.feedback_id}.json"
    path.write_text(json.dumps(envelope.to_dict(), ensure_ascii=False, indent=2))
    return path


def feedback_from_assessment_dict(data: dict, *,
                                  decision_ref: str | None = None,
                                  ledger_path: Path | str | None = None,
                                  ) -> FeedbackEnvelope:
    """Build an envelope from a stored/exported assessment JSON (both modes).

    L1 for L2 archives: lets MOSS (or ops) retrofit feedback from existing
    assessments without re-running them.
    """
    findings = data.get("findings", [])
    acceptance = data.get("acceptance")
    counts: dict = {}
    if isinstance(acceptance, list):
        counts = {
            "evidenced": sum(1 for a in acceptance if a.get("evidenced")),
            "gaps": sum(1 for a in acceptance if not a.get("evidenced")),
        }
    return build_feedback(
        assessment_id=str(data.get("assessment_id") or f"ASM-EXT-{data.get('created_at', 'na')}"),
        subject=str(data.get("subject") or data.get("project") or "?"),
        status=str(data.get("status") or "INCONCLUSIVE"),
        findings=findings,
        kind=str(data.get("kind") or "item"),
        decision_ref=decision_ref or data.get("decision_ref"),
        reassessment_of=data.get("reassessment_of"),
        acceptance_counts=counts,
        ledger_path=ledger_path,
    )
