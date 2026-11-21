"""Verifier plugin SDK (v1, Experimental).

Deterministic verifiers extend ``asr assess`` beyond pytest: lint-class
checks, security scans, licence gates — anything that turns project facts
into findings. The SDK is deliberately minimal:

- A verifier is any object with ``name`` and ``run(project_dir) -> findings``.
- Registration is explicit (``register_verifier``) or via the
  ``modou.verifiers`` entry-point group (third-party packages).
- Verifier findings join the assessment like any other finding; a raising
  verifier degrades to a ``VERIFIER_ERROR`` finding — it can never turn a
  run into PASS and never crashes the assessment (fail-safe discipline).
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from asr.assurance import Finding

_VERIFIERS: dict[str, VerifierHook] = {}


@runtime_checkable
class VerifierHook(Protocol):
    """Contract for a deterministic verifier plugin."""

    name: str

    def run(self, project_dir: Path) -> list[Finding]:
        """Inspect the project (read-only) and return findings ([] = clean)."""
        ...


def register_verifier(verifier: VerifierHook) -> None:
    if not verifier.name:
        raise ValueError("verifier must have a non-empty name")
    _VERIFIERS[verifier.name] = verifier


def load_entry_point_verifiers() -> list[str]:
    """Discover third-party verifiers via the ``modou.verifiers`` group."""
    loaded: list[str] = []
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="modou.verifiers")
    except Exception:  # pragma: no cover — very old pythons / broken env
        return loaded
    for ep in eps:
        try:
            obj = ep.load()
            verifier = obj() if isinstance(obj, type) else obj
            register_verifier(verifier)
            loaded.append(verifier.name)
        except Exception:  # noqa: BLE001 — a broken plugin must not break discovery
            continue
    return loaded


def get_verifier(name: str) -> VerifierHook:
    if name not in _VERIFIERS:
        load_entry_point_verifiers()
    if name not in _VERIFIERS:
        try:  # built-ins register on demand, third-parties via entry points
            from asr.verifiers import register_builtins
            register_builtins()
        except ImportError:  # pragma: no cover
            pass
    if name not in _VERIFIERS:
        raise KeyError(
            f"unknown verifier {name!r} (known: {sorted(_VERIFIERS)})")
    return _VERIFIERS[name]


def run_verifier_safely(verifier: VerifierHook, project_dir: Path) -> list[Finding]:
    """Run one verifier; failures degrade to a finding, never a crash."""
    try:
        findings = verifier.run(project_dir)
    except Exception as e:  # noqa: BLE001 — plugin isolation boundary
        return [Finding(
            code="VERIFIER_ERROR",
            message=f"verifier {verifier.name!r} raised {type(e).__name__}: {e}",
            severity="LOW",
            source=f"verifier:{verifier.name}",
        )]
    for f in findings:
        # provenance: findings carry their producing verifier
        if not f.source:
            f.source = f"verifier:{verifier.name}"
    return findings


def registered() -> list[str]:
    return sorted(_VERIFIERS)


# Verifier factory type for programmatic registration convenience.
VerifierFactory = Callable[[], VerifierHook]
