"""Tests for tiering router (Final-1.0 §4)."""

import pytest

from asr.adapters.ousheng.snapshot import load_snapshot
from asr.assurance.grading import T0, T1, T2, grade_board
from tests.board_fixture import build_board


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    board = build_board(tmp_path_factory.mktemp("grading"))
    return load_snapshot(board)


def test_t2_notice_cancelled_backlog(snap):
    g = grade_board(snap)
    assert g.tiers["N-301"] == T2 and "notice" in g.reasons["N-301"]
    assert g.tiers["BUG-302"] == T2
    assert g.tiers["T-303"] == T2  # backlog: no deliverable


def test_t0_design_closure(snap):
    g = grade_board(snap)
    for iid in ("T-101", "T-102", "T-103", "T-104"):
        assert g.tiers[iid] == T0, iid


def test_t0_breaking_contract(snap):
    g = grade_board(snap)
    assert g.tiers["T-105"] == T0
    assert "breaking" in g.reasons["T-105"]


def test_t0_release_axis(snap):
    g = grade_board(snap)
    assert g.tiers["T-106"] == T0
    assert "release" in g.reasons["T-106"].lower() or "target_version" in g.reasons["T-106"]


def test_t1_ordinary_done(snap):
    g = grade_board(snap)
    assert g.tiers["BUG-201"] == T1


def test_sampling_injectable(snap):
    g = grade_board(snap, sample_rate=1.0, sampler=lambda n: [True] * n)
    assert g.sample_pick == {"BUG-201"}
    g0 = grade_board(snap, sample_rate=0.0)
    assert g0.sample_pick == set()


def test_counts(snap):
    g = grade_board(snap)
    c = g.counts()
    assert c[T0] == 6 and c[T1] == 1 and c[T2] == 3
