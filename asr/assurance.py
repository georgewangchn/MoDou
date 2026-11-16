"""MoDou Assurance Engine — 独立评估（只读）。

职责边界（对应《三 Runtime 统一体系最终方案》§3.1、§6.3、§6.8）：
- Verifier：运行 pytest，给出确定性的事实判定（地面真值）。
- Acceptance Checker：对照验收标准登记证据；没有证据不判 PASS。
- Analyzer：语义偏差分析，只提供 finding 与建议，不覆盖 Verifier 的事实判定。
- Builder：不参与。本模块不 import、不实例化、不调用任何 Builder。

副作用约束：不修改被评估项目的交付文件；pytest 的 __pycache__ 由
PYTHONDONTWRITEBYTECODE=1 抑制，缓存插件关闭。Analyzer 的临时沙箱由
AnalyzerAgent 自行创建并删除。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from asr.agents.tester import _parse_pytest_output
from asr.config.models import AgentConfig
from asr.spec.models import Specification

STATUS_PASS = "PASS"
STATUS_DEVIATION = "DEVIATION"
STATUS_INCONCLUSIVE = "INCONCLUSIVE"
STATUS_BLOCKED = "BLOCKED"

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"

_ANALYZER_CODE_MAP = {
    "MISSING": "OMISSION",
    "LOGIC": "SEMANTIC_DRIFT",
    "CONSTRAINT": "CONSTRAINT_VIOLATION",
    "DEVIATION": "SEMANTIC_DRIFT",
}


@dataclass
class Finding:
    """统一偏差发现（方案文档 §6.7：稳定 code + 独立 severity）。"""

    code: str
    message: str
    severity: str = SEVERITY_MEDIUM
    confidence: float | None = None
    source: str = "verifier"  # verifier | acceptance | analyzer
    evidence: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Verification:
    """Verifier 的确定性结果。"""

    command: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failures: list[dict] = field(default_factory=list)
    exit_code: int | None = None
    test_node_ids: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "pass_rate": round(self.pass_rate, 4),
            "failures": self.failures,
            "exit_code": self.exit_code,
        }


@dataclass
class Assessment:
    """一次独立评估的完整结论（Assessment Contract 的最小实现）。"""

    status: str
    project: str
    spec_source: str
    goal: str
    verification: Verification
    findings: list[Finding] = field(default_factory=list)
    acceptance: list[dict[str, Any]] = field(default_factory=list)
    analyzer_mode: str = "skipped"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project": self.project,
            "spec_source": self.spec_source,
            "goal": self.goal,
            "verification": self.verification.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "acceptance": self.acceptance,
            "analyzer_mode": self.analyzer_mode,
            "created_at": self.created_at,
        }

    def to_text(self) -> str:
        v = self.verification
        lines = [
            f"MoDou 独立评估 — {self.project}",
            f"状态: {self.status}",
            f"验证: pytest {v.passed}/{v.total} passed"
            + (f", {v.failed} failed, {v.errors} errors" if v.failed or v.errors else ""),
        ]
        if self.acceptance:
            evidenced = sum(1 for a in self.acceptance if a.get("evidenced"))
            lines.append(f"验收: {evidenced}/{len(self.acceptance)} 项有证据")
        lines.append(f"语义分析: {self.analyzer_mode}")
        if self.findings:
            lines.append("发现:")
            for f in self.findings:
                conf = f" conf={f.confidence}" if f.confidence is not None else ""
                lines.append(f"  [{f.code}][{f.severity}][{f.source}{conf}] {f.message}")
                for ev in f.evidence[:2]:
                    lines.append(f"      evidence: {ev}")
        return "\n".join(lines)


def _passed_node_ids(stdout: str) -> list[str]:
    """从 pytest -v 输出中提取通过的 nodeid，供验收证据匹配。"""
    ids: list[str] = []
    for line in stdout.splitlines():
        if "::" in line and re.search(r"\sPASSED\b", line):
            ids.append(line.strip().split(" ", 1)[0])
    return ids


def _bullets_under(text: str, headings: tuple[str, ...]) -> list[str]:
    lines = text.splitlines()
    collected: list[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            active = any(h.lower() in stripped.lower() for h in headings)
            continue
        if active and re.match(r"^[-*•]\s+", stripped):
            collected.append(re.sub(r"^[-*•]\s+", "", stripped).strip())
    return collected


def _load_spec(project_dir: Path, spec_path: Path | None) -> tuple[Specification, str]:
    """加载验收基准。YAML 优先，其次 DESIGN.md 的轻量投影。"""
    if spec_path is not None:
        data = yaml.safe_load(spec_path.read_text()) or {}
        return Specification(**data), str(spec_path)

    design = project_dir / "DESIGN.md"
    if design.exists():
        text = design.read_text(errors="replace")
        goal = next(
            (ln.strip().lstrip("# ") for ln in text.splitlines() if ln.strip()),
            "",
        )
        constraints = [
            {"name": c, "type": "must"} for c in _bullets_under(text, ("Constraints", "约束"))
        ]
        acceptance = [
            {"name": a} for a in _bullets_under(
                text, ("Acceptance", "验收", "Testing Strategy", "测试策略")
            )
        ]
        return Specification(
            goal=goal, constraints=constraints, acceptance=acceptance
        ), str(design)

    return Specification(goal=""), "(none)"


def run_verification(
    project_dir: Path,
    *,
    timeout: int = 600,
    basetemp: Path | None = None,
    pytest_args: list[str] | None = None,
) -> tuple[Verification, str]:
    """只读运行 pytest。返回 (Verification, stdout)。

    不生成测试、不同步 sandbox、不回写项目。pytest 的缓存与字节码写入被关闭；
    basetemp 由调用方决定（沙箱环境下可指向工作区内目录）。
    """
    cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short", "-p", "no:cacheprovider"]
    if basetemp is not None:
        cmd += ["--basetemp", str(basetemp)]
    # 不强制传入项目根目录：让 pytest 使用项目自身配置（如 pyproject.toml
    # 的 testpaths）决定收集范围，避免误收集被排除的目录。
    cmd += pytest_args or []

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(project_dir), env=env,
        )
    except subprocess.TimeoutExpired as exc:
        v = Verification(command=" ".join(cmd), exit_code=None)
        return v, f"TIMEOUT: pytest exceeded {timeout}s\n{exc}"
    except FileNotFoundError as exc:
        v = Verification(command=" ".join(cmd), exit_code=None)
        return v, f"ENVIRONMENT_ERROR: {exc}"

    parsed = _parse_pytest_output(proc.stdout)
    v = Verification(
        command=" ".join(cmd),
        total=parsed["total"],
        passed=parsed["passed"],
        failed=parsed["failed"],
        errors=parsed["errors"],
        failures=parsed["failures"],
        exit_code=proc.returncode,
        test_node_ids=_passed_node_ids(proc.stdout),
    )
    return v, proc.stdout


def _check_acceptance(
    spec: Specification, verification: Verification
) -> tuple[list[dict[str, Any]], list[Finding]]:
    """登记证据；匹配不到的标准输出 EVIDENCE_GAP，不判失败也不判通过。"""
    results: list[dict[str, Any]] = []
    gaps: list[Finding] = []
    node_ids = [n.lower() for n in verification.test_node_ids]

    for ac in spec.acceptance:
        tokens = [t.lower() for t in re.split(r"[\s_\-:：/（）()]+", ac.name) if len(t) >= 3]
        matched = [n for n in node_ids if any(t in n for t in tokens)] if tokens else []
        evidenced = bool(matched and not verification.failures)
        results.append({
            "name": ac.name,
            "expected_behavior": ac.expected_behavior,
            "evidenced": evidenced,
            "evidence": matched[:3],
        })
        if not evidenced:
            gaps.append(Finding(
                code="EVIDENCE_GAP",
                message=f"验收标准「{ac.name}」未匹配到通过的测试证据",
                severity=SEVERITY_MEDIUM,
                source="acceptance",
                evidence=[],
            ))
    return results, gaps


async def _analyzer_findings(
    project_dir: Path,
    verification: Verification,
    *,
    use_analyzer: bool,
) -> tuple[list[Finding], str]:
    """语义偏差分析。不可用时返回 INCONCLUSIVE 标记，不伪造 PASS。"""
    if not use_analyzer:
        return [], "skipped"
    if not shutil.which("opencode"):
        return [Finding(
            code="ANALYSIS_UNAVAILABLE",
            message="opencode CLI 不可用，语义偏差分析未执行",
            severity=SEVERITY_LOW,
            source="analyzer",
        )], "unavailable"

    from asr.agents.analyzer import AnalyzerAgent
    from asr.config.loader import create_default_config
    from asr.events.store import EventStore

    config = create_default_config()
    agent_config = config.get_agent("analyzer") or AgentConfig(role="analyzer")
    with tempfile.TemporaryDirectory(prefix="modou-assess-") as tmp:
        store = EventStore(
            event_dir=Path(tmp) / "events",
            inbox_dir=Path(tmp) / "inbox",
            patches_dir=Path(tmp) / "patches",
            diffs_dir=Path(tmp) / "diffs",
            state_dir=Path(tmp) / "state",
            tasks_dir=Path(tmp) / "tasks",
        )
        agent = AnalyzerAgent(agent_config, store, project_dir)
        test_summary = {
            "total": verification.total,
            "passed": verification.passed,
            "failed": verification.failed,
            "failures": verification.failures,
        }
        try:
            report = await agent._analyze(test_summary, "")
        except Exception as exc:
            return [Finding(
                code="ANALYSIS_ERROR",
                message=f"语义分析失败: {type(exc).__name__}: {exc}",
                severity=SEVERITY_LOW,
                source="analyzer",
            )], "error"

    if report.aligned:
        return [], "completed"

    findings: list[Finding] = []
    for sf in report.structured_findings:
        findings.append(Finding(
            code=_ANALYZER_CODE_MAP.get(sf.category.upper(), "SEMANTIC_DRIFT"),
            message=sf.message,
            severity=sf.severity.upper() if sf.severity.upper() in
            (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, "CRITICAL") else SEVERITY_MEDIUM,
            confidence=sf.confidence,
            source="analyzer",
            evidence=sf.evidence,
            affected_files=sf.affected_files,
        ))
    if not findings:
        findings.append(Finding(
            code="INCONCLUSIVE",
            message="Analyzer 产出非结构化结论，无法形成可核验的 finding",
            severity=SEVERITY_LOW,
            source="analyzer",
            evidence=[(report.full_text or "")[:200]],
        ))
    return findings, "completed"


def _has_collection_error(stdout: str) -> bool:
    return "error during collection" in stdout or "Interrupted:" in stdout


def _determine_status(
    verification: Verification,
    findings: list[Finding],
    acceptance: list[dict[str, Any]],
    spec_source: str,
    stdout: str,
) -> str:
    """状态规则：事实判定优先；证据不足不得默认 PASS。"""
    if stdout.startswith(("TIMEOUT:", "ENVIRONMENT_ERROR:")):
        return STATUS_BLOCKED
    if _has_collection_error(stdout):
        # 验证器无法收集测试：交付状态不可确认，且不是验证器自身故障
        return STATUS_DEVIATION
    if verification.total == 0:
        return STATUS_INCONCLUSIVE

    blocking = [
        f for f in findings
        if f.severity in (SEVERITY_HIGH, "CRITICAL")
        or (f.confidence is not None and f.confidence >= 0.7 and f.code != "EVIDENCE_GAP")
    ]
    if verification.failed or verification.errors or blocking:
        return STATUS_DEVIATION

    # 没有可核验的验收标准时，测试全绿只说明“执行完成”，不等于“目标达成”（§3.2）
    if not acceptance or not all(a.get("evidenced") for a in acceptance):
        return STATUS_INCONCLUSIVE
    return STATUS_PASS


async def assess(
    project_dir: Path,
    spec_path: Path | None = None,
    *,
    use_analyzer: bool = True,
    timeout: int = 600,
    basetemp: Path | None = None,
    pytest_args: list[str] | None = None,
) -> Assessment:
    """执行一次独立评估。不调用 Builder，不修改交付文件。"""
    project_dir = project_dir.resolve()
    spec, spec_source = _load_spec(project_dir, spec_path)

    verification, stdout = run_verification(
        project_dir, timeout=timeout, basetemp=basetemp, pytest_args=pytest_args
    )

    acceptance, gaps = _check_acceptance(spec, verification)

    spec_errors = spec.validate_spec().errors
    if spec_errors:
        gaps.append(Finding(
            code="SPEC_INCOMPLETE",
            message="验收基准不完整: " + "; ".join(spec_errors),
            severity=SEVERITY_MEDIUM,
            source="acceptance",
        ))
    if verification.total == 0 and not stdout.startswith(("TIMEOUT:", "ENVIRONMENT_ERROR:")):
        gaps.append(Finding(
            code="EVIDENCE_GAP",
            message="未发现可执行的测试，Verifier 无法给出事实判定",
            severity=SEVERITY_HIGH,
            source="verifier",
        ))
    if verification.failed or verification.errors:
        gaps.append(Finding(
            code="QUALITY_FAILURE",
            message=f"Verifier 判定失败: {verification.failed} failed, {verification.errors} errors",
            severity=SEVERITY_HIGH,
            source="verifier",
            evidence=[f.get("nodeid", "") for f in verification.failures[:3]],
        ))
    if _has_collection_error(stdout):
        gaps.append(Finding(
            code="COLLECTION_ERROR",
            message="Verifier 无法收集测试，交付状态不可确认",
            severity=SEVERITY_HIGH,
            source="verifier",
            evidence=[ln for ln in stdout.splitlines() if "ERROR" in ln][:3],
        ))

    analyzer_findings, analyzer_mode = await _analyzer_findings(
        project_dir, verification, use_analyzer=use_analyzer
    )

    findings = gaps + analyzer_findings
    status = _determine_status(verification, findings, acceptance, spec_source, stdout)
    return Assessment(
        status=status,
        project=str(project_dir),
        spec_source=spec_source,
        goal=spec.goal,
        verification=verification,
        findings=findings,
        acceptance=acceptance,
        analyzer_mode=analyzer_mode,
    )
