"""Cross-check taxonomy: MODOU findings vs OuSheng converge output.

Final-1.0 §6.3: the four-way classification (CONFIRMED_OVERLAP /
MODOU_ONLY / CONVERGE_ONLY / DISAGREEMENT) is a *report metric*, never a
gate. Overlap is defence-in-depth; converging on the same answer is not
a failure. DISAGREEMENT needs semantic alignment (P2+) — same subject
with contradictory verdicts — and is only detectable once both sides
produce per-item verdicts, so this module marks it N/A for now.

Converge text format (stable de-facto):
  "BLOCKED" / "IN_PROGRESS" / "CONVERGED" header,
  "  - <ID>: <blocker>" blockers, "  ! <ID>: <warning>" warnings,
  design lines "  ! design <topic>: …".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ID_RE = re.compile(r"\b([A-Z]{1,6}-\d{1,4})\b")
_DESIGN_RE = re.compile(r"\bdesign ([a-z0-9-]+):")


@dataclass
class CrossCheck:
    confirmed_overlap: list[str] = field(default_factory=list)  # finding ids (code@subject)
    modou_only: list[str] = field(default_factory=list)
    converge_only: list[str] = field(default_factory=list)      # converge lines
    disagreement: list[str] = field(default_factory=list)       # P2+: N/A
    converge_status: str = ""

    def to_dict(self) -> dict:
        return {
            "converge_status": self.converge_status,
            "confirmed_overlap": self.confirmed_overlap,
            "modou_only": self.modou_only,
            "converge_only": self.converge_only,
            "disagreement": self.disagreement,
            "note": "DISAGREEMENT requires semantic verdict alignment (P2+); N/A here",
        }


def parse_converge(text: str) -> tuple[str, set[str], set[str], list[str]]:
    """→ (status, warned_item_ids, warned_design_topics, warning_lines)."""
    status = ""
    items: set[str] = set()
    designs: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s in ("BLOCKED", "IN_PROGRESS", "CONVERGED") and not status:
            status = s
            continue
        if s.startswith(("- ", "! ")):
            body = s[2:]
            lines.append(body)
            if body.startswith("design "):
                if m := _DESIGN_RE.search(body + ":"):
                    designs.add(m.group(1))
            else:
                items.update(_ID_RE.findall(body))
    return status, items, designs, lines


def cross_check(report: dict, converge_text: str) -> CrossCheck:
    """Classify MODOU findings against converge warnings (metric, not gate)."""
    cc = CrossCheck()
    cc.converge_status, items, designs, warn_lines = parse_converge(converge_text)

    finding_keys: list[tuple[str, str, str]] = []  # (key, subject, kind)
    for a in report.get("assessments", []):
        kind = a.get("kind")
        for f in a.get("findings", []):
            subj = (f.get("subject") or {}).get("work_item") or a.get("subject") or ""
            if subj:
                finding_keys.append((f"{f['code']}@{subj}", subj, kind))
    # design-level findings overlap on the design topic axis
    for key, subj, kind in finding_keys:
        if (kind == "design" and subj in designs) or subj in items:
            cc.confirmed_overlap.append(key)
        else:
            cc.modou_only.append(key)

    modou_subjects = {s for _, s, _ in finding_keys}
    for w in warn_lines:
        wids = set(_ID_RE.findall(w)) & items
        dm = _DESIGN_RE.search(w + ":")
        hit = (dm and dm.group(1) in modou_subjects) or (wids & modou_subjects)
        if not hit:
            cc.converge_only.append(w)
    return cc
