"""Board assessment pipeline — mode C orchestration (shadow, read-only).

Snapshot → grade → dossiers (design two-level + sampled items) → LLM
assessor (or --no-llm plumbing mode) → gates → compose → archive.
Nothing here ever writes to the board; the only output is MODOU-owned
assessment files (default-shadow discipline).
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from asr.adapters.ousheng.snapshot import load_snapshot
from asr.assurance.assessor import AssessorResult, assess_dossier
from asr.assurance.compose import (
    Assessment, AssessorVerdict, GateConfig, apply_gates, compose_assessment,
    make_fingerprint,
)
from asr.assurance.dossier import build_design_dossier, build_item_dossier
from asr.assurance.grading import grade_board
from asr.assurance.store import AssessmentStore

EXIT_PASS, EXIT_DEVIATION, EXIT_BLOCKED = 0, 1, 2


@dataclass
class BoardAssessReport:
    board: str = ""
    as_of_commit: str = ""
    analyzer_mode: str = "llm"
    tier_coverage: dict = field(default_factory=dict)
    sample_rate: float = 0.0
    assessments: list[Assessment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def exit_code(self) -> int:
        if any(a.status == "BLOCKED" for a in self.assessments) or self.errors:
            return EXIT_BLOCKED
        if any(a.status in ("DEVIATION", "INCONCLUSIVE") for a in self.assessments):
            return EXIT_DEVIATION
        return EXIT_PASS

    def to_dict(self) -> dict:
        return {
            "board": self.board,
            "as_of_commit": self.as_of_commit,
            "analyzer_mode": self.analyzer_mode,
            "sample_rate": self.sample_rate,
            "tier_coverage": self.tier_coverage,
            "errors": self.errors,
            "assessments": [a.to_dict() for a in self.assessments],
            "exit_code": self.exit_code(),
        }


def _stamp_fingerprints(assessments: list[Assessment], rev: str) -> None:
    for a in assessments:
        for f in a.findings:
            subject_id = (f.subject.get("work_item") or a.subject or "")
            f.fingerprint = make_fingerprint(str(subject_id), f.code, rev,
                                             str(f.subject.get("revision") or rev))


async def run_board_assessment(
    board_dir: Path | str,
    rev: str = "HEAD",
    designs: list[str] | None = None,
    items: list[str] | None = None,
    sample_rate: float = 0.0,
    rules_file: Path | str | None = None,
    rules_ref: str | None = None,
    no_llm: bool = False,
    workdir: Path | str | None = None,
    gate_cfg: GateConfig | None = None,
    reassess_of: str | None = None,
    store: AssessmentStore | None = None,
) -> BoardAssessReport:
    board_dir = Path(board_dir)
    snap = load_snapshot(board_dir, rev)
    gate_cfg = gate_cfg or GateConfig()

    graded = grade_board(snap, sample_rate=sample_rate)
    rules_text = Path(rules_file).read_text() if rules_file else None

    report = BoardAssessReport(
        board=str(snap.project.get("id") or board_dir.name),
        as_of_commit=rev,
        analyzer_mode="none (plumbing)" if no_llm else "llm",
        tier_coverage=graded.counts(),
        sample_rate=sample_rate,
    )

    # Job list: explicit designs (default: all decided) + explicit items + T1 sample
    design_topics = [t for t in snap.decided_designs
                     if not designs or t in designs]
    missing = [t for t in (designs or []) if t not in snap.designs]
    if missing:
        raise ValueError(f"unknown design topic(s): {missing}")

    jobs: list[tuple[str, object]] = [
        ("design", build_design_dossier(snap, snap.designs[t],
                                        rules_text=rules_text, rules_ref=rules_ref))
        for t in design_topics
    ]
    for iid in items or []:
        if iid not in snap.work_items:
            raise ValueError(f"unknown work item: {iid}")
        jobs.append(("item", build_item_dossier(snap, snap.work_items[iid],
                                                rules_text=rules_text, rules_ref=rules_ref)))
    for iid in sorted(graded.sample_pick):
        jobs.append(("item", build_item_dossier(snap, snap.work_items[iid],
                                                rules_text=rules_text, rules_ref=rules_ref)))

    if not jobs:
        report.errors.append("nothing to assess (no decided designs, items or sample)")
        return report

    staging = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="modou-assess-"))
    staging.mkdir(parents=True, exist_ok=True)

    for kind, dossier in jobs:
        baseline_refs = ([{"type": "design", "source": dossier.meta.get("design", ""),
                           "git_rev": rev}] if kind == "design"
                         else [{"type": "item", "source": dossier.subject, "git_rev": rev}])
        scope = {"board": report.board, "as_of_commit": rev,
                 "dossier_pages": sorted(dossier.pages),
                 "tier": "T0" if kind == "design" else "T1-sample"}

        if no_llm:
            verdict = AssessorVerdict(insufficient_evidence=True)
            result = AssessorResult(verdict, raw_text="", parse_ok=True,
                                    parse_notes=["no-llm plumbing mode: INCONCLUSIVE by construction"])
        else:
            try:
                result = await assess_dossier(dossier, staging)
                verdict = result.verdict
            except Exception as e:
                a = Assessment(assessment_id="", kind=kind, subject=dossier.subject,
                               scope=scope, baseline_refs=baseline_refs,
                               reassessment_of=reassess_of)
                a.assessment_id = _blocked_id()
                a.status = "BLOCKED"
                a.assurance = {k: "UNASSESSED" for k in
                               ("governance", "execution", "deliverable", "outcome")}
                a.dropped_findings = [{"code": "N/A", "reason": f"assessor error: {e}"}]
                report.assessments.append(a)
                continue

        gates = apply_gates(verdict.findings, set(dossier.pages), gate_cfg)
        a = compose_assessment(verdict, kind, dossier.subject, scope,
                               baseline_refs, gates, reassessment_of=reassess_of)
        if result.parse_notes:
            a.scope["parse_notes"] = result.parse_notes
        report.assessments.append(a)

    _stamp_fingerprints(report.assessments, rev)
    if store:
        for a in report.assessments:
            store.save(a)
    return report


def _blocked_id() -> str:
    from asr.assurance.compose import new_assessment_id
    return new_assessment_id()
