"""Public API surface tests (asr/__init__.py, modou/public-api@1)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


def test_all_exports_are_importable():
    import asr
    for name in asr.__all__:
        assert hasattr(asr, name), f"__all__ entry missing: {name}"


def test_version_matches_pyproject():
    import asr
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    for line in pyproject.read_text().splitlines():
        if line.startswith("version"):
            assert asr.__version__ == line.split("=")[1].strip().strip('"')
            return
    pytest.fail("version not found in pyproject")


def test_run_not_reexported():
    """The convergence plugin mutates projects — it stays CLI-only."""
    import asr
    assert "run" not in asr.__all__


def test_api_version_pinned():
    import asr
    assert asr.API_VERSION == "modou/public-api@1"


def test_assess_programmatic(tmp_path, monkeypatch):
    """Library embedding: drive an assessment without the CLI."""
    import asyncio
    from unittest.mock import patch

    import asr
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- user_login\n", encoding="utf-8")
    stdout = "tests/test_login.py::test_user_login PASSED [100%]\n1 passed\n"

    class FakeProc:
        returncode = 0
        stderr = ""

        @property
        def stdout(self):
            return stdout

    with patch("asr.assurance.project.subprocess.run", return_value=FakeProc()):
        result = asyncio.run(
            asr.assess(tmp_path, use_analyzer=False))
    assert result.status == asr.STATUS_PASS
    assert isinstance(result, asr.Assessment)


def test_init_docstring_mentions_contract():
    import asr
    src = Path(asr.__file__).read_text()
    tree = ast.parse(src)
    assert tree.body[0].value.value.startswith("MoDou")  # docstring present
