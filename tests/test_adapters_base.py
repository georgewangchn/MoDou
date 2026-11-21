"""Adapter registry tests (second-organization-runtime contract)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from asr.adapters.base import UnknownOrganization, get_adapter, register_adapter
from asr.assurance.board_model import BoardSnapshot


def test_ousheng_adapter_registered_and_callable(tmp_path):
    from tests.board_fixture import build_board
    board = build_board(tmp_path / "b")
    adapter = get_adapter("ousheng")
    assert adapter.name == "ousheng"
    sha = adapter.resolve_rev(board, "HEAD")
    assert len(sha) == 40
    snap = adapter.load_snapshot(board, sha)
    assert isinstance(snap, BoardSnapshot)
    assert "T-101" in snap.work_items


def test_unknown_org_fails_loud():
    with pytest.raises(UnknownOrganization, match="no organization adapter"):
        get_adapter("franken-runtime")


def test_custom_adapter_registers_without_touching_core(tmp_path):
    """The design promise: a second organization runtime = one new adapter."""
    from asr.adapters.base import get_adapter as ga

    class FakeAdapter:
        name = "fake-org"

        def resolve_rev(self, board_dir, rev):
            return "deadbeef" * 5

        def load_snapshot(self, board_dir, rev):
            return BoardSnapshot(board_dir=board_dir, rev=rev)

    register_adapter(FakeAdapter())
    a = ga("fake-org")
    assert a.resolve_rev(tmp_path, "HEAD") == "deadbeef" * 5
    assert a.load_snapshot(tmp_path, "x").work_items == {}


def test_board_assess_runs_via_registry(tmp_path):
    from asr.cli.main import cli
    from tests.board_fixture import build_board
    board = build_board(tmp_path / "b")
    runner = CliRunner()
    r = runner.invoke(cli, ["board-assess", "--board", str(board), "--no-llm",
                            "--store-dir", str(tmp_path / "s")],
                      catch_exceptions=False)
    assert r.exit_code == 1  # INCONCLUSIVE plumbing mode, via adapter path
