"""Neutral read-only git helpers (audit channel plumbing).

`git show <rev>:<path>` / `cat-file -e` style access is not specific to any
organization runtime — both the OuSheng adapter and the assurance core's
evidence resolver need the same hermetic, read-only invocation. Centralizing
it here keeps the core free of adapter internals. Errors are returned, not
raised: callers decide what a non-zero exit means for their channel.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from asr.envs import hermetic_git_env


def git_read(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a read-only git command against a clone with a hermetic env."""
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        env=hermetic_git_env(),
    )
