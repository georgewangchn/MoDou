"""Built-in verifier plugins (registered on demand, never by default).

Keeping verifier behavior opt-in (``--enable-verifier <name>``) is the
Experimental-capability discipline: assessment verdicts must not silently
change because a plugin ships with the runtime.
"""
from __future__ import annotations

from asr.plugins import register_verifier
from asr.verifiers.compile_check import CompileCheckVerifier


def register_builtins() -> list[str]:
    register_verifier(CompileCheckVerifier())
    return ["compile_check"]


__all__ = ["register_builtins"]
