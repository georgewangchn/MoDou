"""OuSheng adapter — the only module that knows the OuSheng board format.

Registers itself into the organization-adapter registry (anti-corruption
boundary, see :mod:`asr.adapters.base`).
"""
from __future__ import annotations

from pathlib import Path

from asr.adapters.base import register_adapter
from asr.adapters.ousheng.snapshot import SnapshotError, load_snapshot, resolve_rev
from asr.assurance.board_model import BoardSnapshot


class OushengAdapter:
    """Read-side adapter over the OuSheng git board (snapshot channel)."""

    name = "ousheng"

    def resolve_rev(self, board_dir: Path, rev: str) -> str:
        return resolve_rev(board_dir, rev)

    def load_snapshot(self, board_dir: Path, rev: str) -> BoardSnapshot:
        return load_snapshot(board_dir, rev)


def ensure_registered() -> OushengAdapter:
    adapter = OushengAdapter()
    register_adapter(adapter)
    return adapter


__all__ = ["OushengAdapter", "ensure_registered", "SnapshotError"]
