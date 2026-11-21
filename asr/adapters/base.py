"""Organization-runtime adapter protocol (anti-corruption boundary).

The assurance core speaks to organization runtimes (OuSheng today, others
later) exclusively through this protocol. An adapter owns everything
format-specific — directory layout, schema versions, YAML parsing, the
official write channel — and surfaces neutral :mod:`asr.assurance.board_model`
types. Integrating a second organization runtime must not touch the core:
register an adapter here and pass ``org=<name>``.

Hard constraints (design baseline §1): merging must never require either
side to change code for the other; the writeback channel stays the adapter's
own (MCP for OuSheng) and is not part of this protocol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from asr.assurance.board_model import BoardSnapshot


@runtime_checkable
class OrganizationAdapter(Protocol):
    """Read contract every organization adapter must fulfil."""

    name: str

    def resolve_rev(self, board_dir: Path, rev: str) -> str:
        """Pin a symbolic rev (HEAD) to its immutable sha."""
        ...

    def load_snapshot(self, board_dir: Path, rev: str) -> BoardSnapshot:
        """Load and validate the board at an immutable revision, fail-loud."""
        ...


_REGISTRY: dict[str, OrganizationAdapter] = {}


class UnknownOrganization(RuntimeError):
    """Raised when no adapter is registered for the requested org."""


def register_adapter(adapter: OrganizationAdapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get_adapter(name: str = "ousheng") -> OrganizationAdapter:
    if name not in _REGISTRY:
        # lazy import: base must not depend on concrete adapters at load time
        if name == "ousheng":
            from asr.adapters.ousheng import ensure_registered
            ensure_registered()
        else:
            raise UnknownOrganization(
                f"no organization adapter registered for {name!r} "
                f"(known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]
