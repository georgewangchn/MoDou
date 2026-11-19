"""Tests for dossier assembly (Final-1.0 §5.2)."""
from pathlib import Path

import pytest

from asr.adapters.ousheng.snapshot import load_snapshot
from asr.assurance.dossier import (
    build_design_dossier, build_item_dossier,
)
from tests.board_fixture import build_board


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    board = build_board(tmp_path_factory.mktemp("dossier"))
    return load_snapshot(board)


def test_design_dossier_pages(snap):
    d = build_design_dossier(snap, snap.designs["layered"], rules_text="规则书vX", rules_ref="wth@abc")
    for pid in ("baseline/layered/design.md", "baseline/layered/frontmatter",
                "tasks/T-101", "tasks/T-102", "tasks/T-103", "tasks/T-104",
                "seam/contracts", "rules/ousheng", "context"):
        assert pid in d.pages, pid
    # T-102/T-105 have contracts; T-105 not in closure → seam only has T-102
    assert "T-102" in d.pages["seam/contracts"]
    assert "T-105" not in d.pages["seam/contracts"]


def test_evidence_resolution_internal_doc(snap):
    d = build_design_dossier(snap, snap.designs["layered"])
    page = d.pages["evidence/T-101/1"]
    assert "resolution: RESOLVED" in page
    assert "pytest 12 passed" in page  # content from docs/acceptance/T-101.md


def test_evidence_resolution_commit_needs_repo_mapping(snap):
    d = build_item_dossier(snap, snap.work_items["T-105"])
    page = d.pages["evidence/T-105/1"]
    assert "resolution: UNRESOLVED" in page
    assert "code-repo mapping" in page


def test_missing_evidence_page_absent(snap):
    d = build_item_dossier(snap, snap.work_items["BUG-201"])  # no evidence
    assert not [p for p in d.pages if p.startswith("evidence/")]


def test_item_dossier_design_anchor(snap):
    # T-101 title carries "design:layered" weak link
    d = build_item_dossier(snap, snap.work_items["T-101"])
    assert "baseline/layered/design.md" in d.pages


def test_render_fenced(snap):
    d = build_item_dossier(snap, snap.work_items["T-101"])
    text = d.render()
    assert "===== PAGE tasks/T-101 =====" in text
    assert text.count("```") >= 2 * len(d.pages)


def test_dangling_related_recorded(snap):
    design = snap.designs["layered"]
    design.related_items.append("T-999")
    d = build_design_dossier(snap, design)
    assert d.meta["dangling_related"] == ["T-999"]
