"""Anti-corruption layer tests: neutral board model + adapter isolation."""
from __future__ import annotations

import sys
from pathlib import Path

from asr.assurance.board_model import (
    DESIGN_DECIDED_STATUSES,
    BoardSnapshot,
    DesignRecord,
    EvidenceEntry,
    WorkItem,
)


def test_model_imports_do_not_pull_adapter():
    """The core model module must not depend on any concrete adapter.

    Checked in a fresh interpreter: same-session imports of other test
    modules would pollute sys.modules and fake the isolation.
    """
    import asr.assurance.board_model as bm
    src = Path(bm.__file__).read_text()
    assert "ousheng" not in src.lower()

    import subprocess
    code = ("import sys; import asr.assurance.board_model; "
            "mods = [m for m in sys.modules if m.startswith('asr.adapters')]; "
            "print(mods)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(Path(__file__).parent.parent))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_t0_closure_and_decided_designs():
    snap = BoardSnapshot(board_dir=Path("/b"), rev="r")
    snap.work_items = {"A": WorkItem(id="A", path="a"), "B": WorkItem(id="B", path="b")}
    snap.designs = {
        "d1": DesignRecord(topic="d1", path="d1.md", status="agreed",
                           related_items=["A", "GHOST"]),
        "d2": DesignRecord(topic="d2", path="d2.md", status="draft",
                           related_items=["B"]),
    }
    assert snap.decided_designs.keys() == {"d1"}
    assert DESIGN_DECIDED_STATUSES == {"agreed", "decided"}
    # ghost ids outside work_items are dropped by the closure
    assert snap.t0_closure() == {"A"}


def test_legacy_adapter_import_path_still_works():
    """Backward compatibility: existing imports from the adapter survive."""
    from asr.adapters.ousheng.snapshot import BoardSnapshot as B2
    from asr.adapters.ousheng.snapshot import WorkItem as W2
    assert B2 is BoardSnapshot and W2 is WorkItem


def test_evidence_entry_defaults():
    e = EvidenceEntry()
    assert (e.type, e.locator, e.result) == ("", "", "")
