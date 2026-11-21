"""Tests for MoDou's independent assessment engine (asr assess)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from asr.assurance import (
    STATUS_BLOCKED,
    STATUS_DEVIATION,
    STATUS_INCONCLUSIVE,
    STATUS_PASS,
    Finding,
    Verification,
    _check_acceptance,
    _determine_status,
    assess,
    run_verification,
)
from asr.cli.main import cli
from asr.spec.models import Specification


def _fake_proc(stdout: str, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


def test_run_verification_parses_pytest_output(tmp_path):
    stdout = (
        "tests/test_x.py::test_a PASSED [ 50%]\n"
        "tests/test_x.py::test_b FAILED [100%]\n"
        "1 failed, 1 passed\n"
    )
    with patch("asr.assurance.project.subprocess.run", return_value=_fake_proc(stdout, 1)) as mock:
        verification, captured = run_verification(tmp_path)

    assert verification.total == 2
    assert verification.passed == 1
    assert verification.failed == 1
    assert verification.test_node_ids == ["tests/test_x.py::test_a"]
    assert captured == stdout
    assert mock.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "no:cacheprovider" in verification.command


def test_run_verification_reports_environment_error(tmp_path):
    with patch("asr.assurance.project.subprocess.run", side_effect=FileNotFoundError("pytest")):
        verification, stdout = run_verification(tmp_path)
    assert stdout.startswith("ENVIRONMENT_ERROR:")
    assert verification.total == 0


def test_check_acceptance_requires_matched_evidence():
    spec = Specification(goal="g", acceptance=[{"name": "user_login"}])
    verified = Verification(total=1, passed=1, test_node_ids=["tests/test_login.py::test_user_login"])
    results, gaps = _check_acceptance(spec, verified)
    assert results[0]["evidenced"] is True
    assert gaps == []

    unverified = Verification(total=1, passed=1, test_node_ids=["tests/test_other.py::test_x"])
    results, gaps = _check_acceptance(spec, unverified)
    assert results[0]["evidenced"] is False
    assert gaps[0].code == "EVIDENCE_GAP"


def test_determine_status_rules():
    green = Verification(total=10, passed=10)
    assert _determine_status(green, [], [{"name": "a", "evidenced": True}], "spec.yaml", "") == STATUS_PASS
    assert _determine_status(
        green, [Finding(code="QUALITY_FAILURE", message="test failed", severity="HIGH")],
        [{"name": "a", "evidenced": True}], "spec.yaml", "",
    ) == STATUS_DEVIATION
    assert _determine_status(
        green, [], [{"name": "a", "evidenced": False}], "spec.yaml", ""
    ) == STATUS_INCONCLUSIVE
    assert _determine_status(green, [], [], "spec.yaml", "") == STATUS_INCONCLUSIVE
    assert _determine_status(Verification(), [], [], "(none)", "") == STATUS_INCONCLUSIVE
    assert _determine_status(green, [], [], "spec.yaml", "TIMEOUT: 600s") == STATUS_BLOCKED


def test_determine_status_analyzer_degradation_never_pass():
    """Analyzer 显式开启但降级（INCONCLUSIVE finding）时不得伪造 PASS。

    回归：模拟实验室 S1-R7 发现语义层失败 + 测试全绿 + 验收有证据
    曾组合出 PASS/exit 0，违反"证据不足不得默认 PASS"铁律。
    """
    green = Verification(total=10, passed=10)
    evidenced = [{"name": "a", "evidenced": True}]
    degraded = Finding(code="INCONCLUSIVE", message="Analyzer 产出非结构化结论",
                       severity="LOW", source="analyzer")
    assert _determine_status(green, [degraded], evidenced, "spec.yaml", "") \
        == STATUS_INCONCLUSIVE
    # DEVIATION 级 finding 仍优先于 INCONCLUSIVE 降级
    both = [degraded, Finding(code="QUALITY_FAILURE", message="x", severity="HIGH")]
    assert _determine_status(green, both, evidenced, "spec.yaml", "") == STATUS_DEVIATION


def test_assess_does_not_invoke_builder(tmp_path):
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- user_login\n", encoding="utf-8"
    )
    stdout = "tests/test_login.py::test_user_login PASSED [100%]\n1 passed\n"
    with patch("asr.assurance.project.subprocess.run", return_value=_fake_proc(stdout, 0)):
        result = asyncio.run(assess(tmp_path, use_analyzer=False))

    assert result.status == STATUS_PASS
    assert result.verification.passed == 1
    assert result.acceptance[0]["evidenced"] is True
    assert all(f.source != "builder" for f in result.findings)
    assert result.analyzer_mode == "skipped"


def test_assess_marks_failed_verification_as_deviation(tmp_path):
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- user_login\n", encoding="utf-8"
    )
    stdout = (
        "tests/test_login.py::test_user_login FAILED [100%]\n"
        "E   AssertionError: expected 1\n"
    )
    with patch("asr.assurance.project.subprocess.run", return_value=_fake_proc(stdout, 1)):
        result = asyncio.run(assess(tmp_path, use_analyzer=False))

    assert result.status == STATUS_DEVIATION
    assert any(f.code == "QUALITY_FAILURE" for f in result.findings)


def test_cli_assess_writes_json_and_returns_exit_code(tmp_path):
    (tmp_path / "DESIGN.md").write_text(
        "# Demo\n\n## Acceptance\n- user_login\n", encoding="utf-8"
    )
    stdout = "tests/test_login.py::test_user_login PASSED [100%]\n1 passed\n"
    output = tmp_path / "assessment.json"

    with patch("asr.assurance.project.subprocess.run", return_value=_fake_proc(stdout, 0)):
        response = CliRunner().invoke(cli, [
            "assess", "--project", str(tmp_path),
            "--no-analyzer", "--json-output", str(output),
        ])

    assert response.exit_code == 0, response.output
    assert "MoDou 独立评估" in response.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["status"] == STATUS_PASS
    assert data["acceptance"][0]["evidenced"] is True
