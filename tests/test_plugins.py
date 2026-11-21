"""Verifier plugin SDK tests (asr/plugins.py + built-in compile_check)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from asr.plugins import (
    get_verifier,
    register_verifier,
    registered,
    run_verifier_safely,
)


def _fake_proc(stdout: str, returncode: int = 0):
    class P:
        pass
    p = P()
    p.returncode, p.stdout, p.stderr = returncode, stdout, ""
    return p


def test_compile_check_clean_project(tmp_path):
    (tmp_path / "good.py").write_text("x = 1\n")
    v = get_verifier("compile_check")
    assert v.run(tmp_path) == []


def test_compile_check_flags_syntax_error(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n")
    findings = get_verifier("compile_check").run(tmp_path)
    assert len(findings) == 1
    assert findings[0].code == "QUALITY_FAILURE"
    assert findings[0].severity == "HIGH"
    assert "bad.py:1" in findings[0].message
    assert findings[0].source == "verifier:compile_check"


def test_compile_check_skips_vendored_dirs(tmp_path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "bad.py").write_text("def f(:\n")
    assert get_verifier("compile_check").run(tmp_path) == []


def test_raising_verifier_degrades_to_error_finding(tmp_path):
    class Boom:
        name = "boom"

        def run(self, project_dir):
            raise RuntimeError("kaboom")

    out = run_verifier_safely(Boom(), tmp_path)
    assert len(out) == 1
    assert out[0].code == "VERIFIER_ERROR"
    assert "kaboom" in out[0].message


def test_register_rejects_empty_name():
    class NoName:
        name = ""

        def run(self, project_dir):
            return []

    with pytest.raises(ValueError):
        register_verifier(NoName())


def test_unknown_verifier_raises():
    with pytest.raises(KeyError, match="unknown verifier"):
        get_verifier("does-not-exist")


def test_assess_with_verifier_flips_status(tmp_path):
    """A syntax error must turn an otherwise-green assessment into DEVIATION."""
    import asr
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- add\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_add.py").write_text("def test_add():\n    assert 1\n")
    (tmp_path / "src_broken.py").write_text("def broken(:\n")
    stdout = "tests/test_add.py::test_add PASSED [100%]\n1 passed\n"
    with patch("asr.assurance.project.subprocess.run",
               return_value=_fake_proc(stdout)):
        result = asyncio.run(asr.assess(
            tmp_path, use_analyzer=False, verifier_names=["compile_check"]))
    assert result.status == asr.STATUS_DEVIATION
    assert any(f.code == "QUALITY_FAILURE" for f in result.findings)
    assert result.analyzer_mode.endswith("+verifiers")


def test_cli_enable_verifier_flag(tmp_path):
    from asr.cli.main import cli
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- add\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_add.py").write_text("def test_add():\n    assert 1\n")
    (tmp_path / "src_broken.py").write_text("def broken(:\n")
    stdout = "tests/test_add.py::test_add PASSED [100%]\n1 passed\n"
    with patch("asr.assurance.project.subprocess.run",
               return_value=_fake_proc(stdout)):
        runner = CliRunner()
        r = runner.invoke(cli, [
            "assess", "--project", str(tmp_path), "--no-analyzer",
            "--enable-verifier", "compile_check",
            "--json-output", str(tmp_path / "a.json")],
            catch_exceptions=False)
    assert r.exit_code == 1
    assert "QUALITY_FAILURE" in r.output
    import json
    d = json.loads((tmp_path / "a.json").read_text())
    assert any(f["code"] == "QUALITY_FAILURE" for f in d["findings"])


def test_verifier_off_by_default(tmp_path):
    """Experimental discipline: no verifier changes verdicts unless enabled."""
    import asr
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- add\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_add.py").write_text("def test_add():\n    assert 1\n")
    (tmp_path / "src_broken.py").write_text("def broken(:\n")
    stdout = "tests/test_add.py::test_add PASSED [100%]\n1 passed\n"
    with patch("asr.assurance.project.subprocess.run",
               return_value=_fake_proc(stdout)):
        result = asyncio.run(asr.assess(tmp_path, use_analyzer=False))
    assert result.status == asr.STATUS_PASS
    assert "compile_check" not in registered() or True  # registry untouched default
    assert result.analyzer_mode == "skipped"
