"""Tests for converge cross-check taxonomy (metric, never a gate)."""
from asr.assurance.xcheck import cross_check, parse_converge

CONVERGE = """BLOCKED
  - TEST-002: blocked
  ! BUG-137: done but progress reported 70%
  ! BUG-176: testing item with non-tester role "dev"
  ! design layered-data-dev: agreed without decide activity on record
  ! .ousheng/architecture/mlake.md (status, mlake): doc stale — latest T-160
"""

REPORT = {
    "assessments": [
        {"kind": "design", "subject": "layered-data-dev", "findings": [
            {"code": "EVIDENCE_GAP", "subject": {"work_item": "T-104"}},
            {"code": "AUTHORIZATION_GAP", "subject": {"work_item": None}},  # design-level
        ]},
        {"kind": "item", "subject": "BUG-137", "findings": [
            {"code": "EXECUTION_DEVIATION", "subject": {"work_item": "BUG-137"}},
        ]},
        {"kind": "item", "subject": "T-206", "findings": [
            {"code": "EVIDENCE_GAP", "subject": {"work_item": "T-206"}},
        ]},
    ]
}


def test_parse_converge():
    status, items, designs, lines = parse_converge(CONVERGE)
    assert status == "BLOCKED"
    assert "TEST-002" in items and "BUG-137" in items and "T-160" in items
    assert designs == {"layered-data-dev"}
    assert len(lines) == 5


def test_cross_check_classification():
    cc = cross_check(REPORT, CONVERGE)
    # T-104 finding: no converge warning mentions T-104 → MODOU_ONLY
    assert "EVIDENCE_GAP@T-104" in cc.modou_only
    # BUG-137 finding: converge warns BUG-137 → CONFIRMED_OVERLAP
    assert "EXECUTION_DEVIATION@BUG-137" in cc.confirmed_overlap
    # design-level finding (subject=design name) overlaps design warning
    assert "AUTHORIZATION_GAP@layered-data-dev" in cc.confirmed_overlap
    # T-206 finding with no converge counterpart → MODOU_ONLY
    assert "EVIDENCE_GAP@T-206" in cc.modou_only
    # converge warnings with no MODOU counterpart → CONVERGE_ONLY
    joined = " | ".join(cc.converge_only)
    assert "BUG-176" in joined and "TEST-002" in joined
    assert "BUG-137" not in joined and "layered-data-dev" not in joined
    # DISAGREEMENT N/A, noted
    assert cc.to_dict()["disagreement"] == []
    assert "N/A" in cc.to_dict()["note"]
