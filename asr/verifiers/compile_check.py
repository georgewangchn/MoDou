"""compile_check — production-code syntax gate (built-in verifier).

ast.parse over every ``*.py`` outside vendored/runtime dirs. A file that
cannot parse is a hard deliverable defect: the finding is HIGH severity,
which feeds DEVIATION in the status rules. Pure stdlib, deterministic,
read-only.
"""
from __future__ import annotations

import ast
from pathlib import Path

from asr.assurance import Finding

_SKIP_DIRS = {
    ".venv", ".venv-video", "node_modules", ".runtime", ".asr_sandbox",
    ".git", ".modou", ".pytest_cache", "__pycache__", "build", "dist",
}


class CompileCheckVerifier:
    name = "compile_check"

    def run(self, project_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        for py in sorted(project_dir.rglob("*.py")):
            if _SKIP_DIRS & set(py.parts):
                continue
            try:
                ast.parse(py.read_text(errors="replace"), filename=str(py))
            except SyntaxError as e:
                findings.append(Finding(
                    code="QUALITY_FAILURE",
                    message=f"SyntaxError in {py.relative_to(project_dir)}:{e.lineno}: {e.msg}",
                    severity="HIGH",
                    source="verifier:compile_check",
                    evidence=[str(py.relative_to(project_dir))],
                ))
        return findings
