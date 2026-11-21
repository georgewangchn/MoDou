"""Subprocess environment helpers.

Git is invoked read-only across the audit channel (board snapshots, watch
fetches, calibration suites). Those subprocesses must not be steered by the
user's global gitconfig (aliases, insteadOf rewrites, credential config) or
block on interactive prompts — but they also must not assume a Homebrew-style
PATH: git may live anywhere (nix, conda, /usr/local, custom prefix), and the
caller's HOME/proxies may be required for fetch auth.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


def hermetic_git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Caller's environment plus the minimal isolation git needs.

    Inherits PATH/HOME/proxies from the current process, then forces
    non-interactive prompts and skips global/system gitconfig files
    (repo-local config still applies).
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    if extra:
        env.update(extra)
    return env
