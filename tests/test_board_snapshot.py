"""Tests for asr.adapters.ousheng.snapshot (audit channel)."""
from pathlib import Path

import pytest

from asr.adapters.ousheng.snapshot import (
    BoardSnapshot, SnapshotError, load_snapshot,
)
from tests.board_fixture import build_board


@pytest.fixture(scope="module")
def board(tmp_path_factory) -> Path:
    return build_board(tmp_path_factory.mktemp("board_snapshot"))


def test_load_snapshot_head(board: Path):
    snap = load_snapshot(board)
    assert snap.rev == "HEAD"
    assert snap.project["id"] == "synth-board"
    assert len(snap.systems["systems"]) == 3
    # 10 work items
    assert len(snap.work_items) == 10
    t101 = snap.work_items["T-101"]
    assert t101.schema_version == 2
    assert t101.status == "done"
    assert t101.evidence[0].type == "document"
    assert t101.evidence[0].locator == "docs/acceptance/T-101.md"


def test_design_frontmatter_parsed(board: Path):
    snap = load_snapshot(board)
    d = snap.designs["layered"]
    assert d.status == "agreed"
    assert d.systems == ["data", "mlake", "ui"]
    assert d.related_items == ["T-101", "T-102", "T-103", "T-104"]
    assert d.decided_by == "pm"
    assert "义务" in d.body


def test_decided_designs_and_t0_closure(board: Path):
    snap = load_snapshot(board)
    assert set(snap.decided_designs) == {"layered"}
    closure = snap.t0_closure()
    assert closure == {"T-101", "T-102", "T-103", "T-104"}


def test_snapshot_pinned_to_rev(board: Path):
    from tests.board_fixture import _git
    # Mutate the worktree after commit; snapshot must still see the old state
    (board / ".ousheng/work/T-101.yaml").write_text("schema_version: 2\nid: T-101\nGARBAGE: true\n")
    snap = load_snapshot(board)  # reads git objects, not the dirty worktree
    assert snap.work_items["T-101"].status == "done"
    _git(board, "checkout", "--", ".ousheng/work/T-101.yaml")


def test_unknown_schema_version_blocks(tmp_path: Path):
    board = build_board(tmp_path)
    bad = board / ".ousheng/work/T-101.yaml"
    bad.write_text(bad.read_text().replace("schema_version: 2", "schema_version: 99", 1))
    from tests.board_fixture import _git
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "bump schema")
    with pytest.raises(SnapshotError, match="schema_version"):
        load_snapshot(board)


def test_broken_yaml_blocks(tmp_path: Path):
    board = build_board(tmp_path)
    (board / ".ousheng/work/BUG-201.yaml").write_text(":\n  - [unclosed")
    from tests.board_fixture import _git
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "break yaml")
    with pytest.raises(SnapshotError, match="parse error|expected"):
        load_snapshot(board)


def test_missing_board_dir(tmp_path: Path):
    with pytest.raises(SnapshotError, match="not found"):
        load_snapshot(tmp_path / "nope")
