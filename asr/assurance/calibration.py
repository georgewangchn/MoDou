"""Calibration: injection suite + metrics + frozen thresholds + PM ledger.

Final-1.0 §6.3 — writeback's only precondition is calibration passing.
Three pieces:

1. ``build_calibration_suite`` — a fixture board with labeled injections
   (ground truth in ``expected.json``). Deterministic expectations:
   each injected case MUST surface its deviation code; each normal case
   MUST produce no kept findings; the insufficient case MUST land
   INCONCLUSIVE (never fabricated PASS/DEVIATION).
2. ``evaluate`` — detection recall / false positives / inconclusive
   correctness against the truth file; ``Thresholds.check`` renders the
   frozen pass/fail verdict (values frozen at P2, do not tune silently).
3. ``QualityLedger`` — PM spot-check marks (tp/fp) per finding
   fingerprint; precision from the ledger is the writeback gate's
   evidence. MODOU-local file, never on the board.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


# ── Frozen thresholds (P2 freeze; changing these requires a documented decision) ──
@dataclass(frozen=True)
class Thresholds:
    detection_rate: float = 1.0     # all injected deterministic deviations found
    false_positive_total: int = 0   # zero kept findings on normal samples
    precision_min: float = 0.8      # PM-marked precision (ledger)
    precision_min_marks: int = 20   # minimum marks before precision counts

    def check(self, m: CalibrationMetrics) -> list[dict]:
        checks = [
            {"criterion": "detection_rate", "value": m.detection_rate(),
             "required": f">= {self.detection_rate}",
             "pass": m.detection_rate() >= self.detection_rate},
            {"criterion": "false_positives", "value": len(m.false_positives),
             "required": f"== {self.false_positive_total}",
             "pass": len(m.false_positives) == self.false_positive_total},
            {"criterion": "inconclusive_discipline", "value": len(m.inconclusive_violations),
             "required": "== 0",
             "pass": not m.inconclusive_violations},
        ]
        return checks

    @staticmethod
    def precision_check(ledger: QualityLedger) -> dict:
        n, p = ledger.precision()
        return {"criterion": "pm_marked_precision", "marks": n, "precision": p,
                "value": f"{p:.0%} (n={n})" if p is not None else f"n/a (n={n})",
                "required": f">= {Thresholds.precision_min} "
                            f"with n >= {Thresholds.precision_min_marks}",
                "pass": n >= Thresholds.precision_min_marks
                        and p is not None
                        and p >= Thresholds.precision_min}


@dataclass
class CalibrationMetrics:
    expected_hits: list[str] = field(default_factory=list)      # "case:CODE"
    expected_misses: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)    # normal case ids
    normal_total: int = 0
    inconclusive_ok: list[str] = field(default_factory=list)
    inconclusive_violations: list[str] = field(default_factory=list)
    injections_total: int = 0

    def detection_rate(self) -> float:
        if self.injections_total == 0:
            return 0.0
        return len(self.expected_hits) / self.injections_total

    def to_dict(self) -> dict:
        return {
            "injections_total": self.injections_total,
            "detection_rate": self.detection_rate(),
            "hits": self.expected_hits, "misses": self.expected_misses,
            "false_positives": self.false_positives, "normal_total": self.normal_total,
            "inconclusive_ok": self.inconclusive_ok,
            "inconclusive_violations": self.inconclusive_violations,
        }


def evaluate(report: dict, truth: dict) -> CalibrationMetrics:
    """Match report assessments against expected.json truth."""
    m = CalibrationMetrics()
    by_subject = {a["subject"]: a for a in report.get("assessments", [])}

    for case in truth.get("cases", []):
        cid = case["id"]
        kind = case.get("kind", "item")
        subject = case.get("subject", cid)
        a = by_subject.get(subject)
        if a is None and kind == "design":
            a = by_subject.get(case.get("design", subject))
        got_codes = [f["code"] for f in (a or {}).get("findings", [])]
        got_status = (a or {}).get("status")

        if case["expect"] == "CLEAN":
            m.normal_total += 1
            if got_codes:
                m.false_positives.append(f"{cid}:{','.join(got_codes)}")
        elif case["expect"] == "INCONCLUSIVE":
            # Unverifiable evidence admits two disciplined outcomes:
            # INCONCLUSIVE (lenient) or a cited EVIDENCE_GAP finding
            # (strict — the UNRESOLVED resolution is itself a confirmed
            # fact). Only fabricated PASS or uncited deviations violate.
            alts = case.get("accept_alternatives", [])
            ok = got_status == "INCONCLUSIVE" or any(
                c in alts for c in got_codes)
            (m.inconclusive_ok if ok
             else m.inconclusive_violations).append(f"{cid}:{got_status}")
        else:  # expect list of codes (any-of sets allowed)
            for _, codes in enumerate(case["expect"] if isinstance(case["expect"][0], list)
                                      else [case["expect"]]):
                m.injections_total += 1
                if any(c in got_codes for c in codes):
                    m.expected_hits.append(f"{cid}:{'+'.join(codes)}")
                else:
                    m.expected_misses.append(f"{cid}:{'+'.join(codes)}→{got_codes}")
    return m


# ── PM spot-check ledger (MODOU-local) ───────────────────────────────────────
class QualityLedger:
    def __init__(self, root: Path | str):
        self.path = Path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def mark(self, fingerprint: str, verdict: str, marked_by: str,
             note: str = "") -> dict:
        if verdict not in ("tp", "fp"):
            raise ValueError("verdict must be tp|fp")
        entry = {"fingerprint": fingerprint, "verdict": verdict,
                 "marked_by": marked_by, "note": note,
                 "at": datetime.now(UTC).isoformat()}
        with self.path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def precision(self) -> tuple[int, float | None]:
        es = self.entries()
        if not es:
            return 0, None
        tp = sum(1 for e in es if e["verdict"] == "tp")
        return len(es), tp / len(es)


# ── Injection suite builder ──────────────────────────────────────────────────
def _git(repo: Path, *args: str) -> None:
    from asr.envs import hermetic_git_env
    env = hermetic_git_env({"GIT_AUTHOR_NAME": "calib", "GIT_AUTHOR_EMAIL": "c@c",
                            "GIT_COMMITTER_NAME": "calib", "GIT_COMMITTER_EMAIL": "c@c"})
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr}")


def _work(board: Path, iid: str, type_: str, title: str, system: str, status: str,
          extra: str = "") -> None:
    (board / f".ousheng/work/{iid}.yaml").write_text(
        f"schema_version: 2\nid: {iid}\ntype: {type_}\ntitle: {title}\n"
        f"system: {system}\nstatus: {status}\nrevision: 1\npriority: P1\n"
        f"assignee: data\nacting_role: dev\naccountable_human: pm\n{extra}")


TRUTH = {"cases": [
    {"id": "T-A", "kind": "item", "expect": "CLEAN"},
    {"id": "T-B", "kind": "item",
     "expect": [["EVIDENCE_GAP", "FALSE_COMPLETION"]]},  # zero-evidence done straddles deliverable/truthfulness domains
    {"id": "T-C", "kind": "item", "expect": ["FALSE_COMPLETION", "EVIDENCE_GAP"]},
    {"id": "T-E", "kind": "item", "expect": ["DEADLINE_BREACH", "EXECUTION_DEVIATION"]},
    {"id": "T-F", "kind": "item", "expect": "INCONCLUSIVE",
     "accept_alternatives": ["EVIDENCE_GAP"]},
    {"id": "T-G", "kind": "item", "expect": [["AUTHORIZATION_GAP", "CONSTRAINT_VIOLATION"]]},
    {"id": "calib", "kind": "design", "subject": "calib", "expect": ["TASK_COVERAGE_GAP"]},
    {"id": "T-D", "kind": "item",
     # same deviation fact (release evidence never covers the target version):
     # equivalent encodings across the deliverable/truthfulness neighbors
     "expect": ["TARGET_MISS", "EVIDENCE_GAP", "FALSE_COMPLETION", "QUALITY_FAILURE"]},
]}


def build_calibration_suite(root: Path) -> Path:
    """Build the injection board + expected.json; returns suite dir."""
    board = root / "board"
    (board / ".ousheng/work").mkdir(parents=True)
    (board / ".ousheng/designs/calib").mkdir(parents=True)
    (board / "docs").mkdir(parents=True)

    (board / ".ousheng/project.yaml").write_text("id: calib-board\nname: Calib\n")
    (board / ".ousheng/systems.yaml").write_text(
        "systems:\n  - id: data\n    name: data\n  - id: mlake\n    name: mlake\n")
    (board / ".ousheng/roles.yaml").write_text("roles:\n  - id: dev\n")

    # T-A normal: independent test evidence (source=test, not the assignee),
    # resolvable report with command+outcome — a genuinely CLEAN case.
    # (A self-authored acceptance doc by the assignee is NOT clean — it is
    # self-certification, exactly what MODOU exists to catch.)
    _work(board, "T-A", "task", "正常交付任务", "data", "testing",
          "description: 实现导出功能\n"
          "evidence:\n  - type: test_result\n    source: test\n"
          "    locator: docs/T-A-test-report.md\n    result: passed\n"
          "    observed_at: \"2026-11-19T10:00:00+08:00\"\n")
    (board / "docs/T-A-test-report.md").write_text(
        "# T-A 独立测试报告（test 席位出具）\n"
        "- 被测构建：data 仓 commit 3f2a1b9（报告出具时该席位 HEAD）\n"
        "- 命令：pytest tests/test_export.py -v\n"
        "- 结果：12 passed, 0 failed，退出码 0\n"
        "- 覆盖：导出全路径 E2E（含边界）\n")
    # T-B: done, no evidence → EVIDENCE_GAP
    _work(board, "T-B", "task", "无证据交付任务", "data", "done",
          "description: 实现导入功能\n")
    # T-C: done, evidence locator dangling → FALSE_COMPLETION
    _work(board, "T-C", "task", "悬空证据任务", "data", "done",
          "evidence:\n  - type: document\n    source: dev\n"
          "    locator: docs/never-committed.md\n    result: passed\n")
    # T-D: release with target_version, evidence never mentions it → TARGET_MISS
    _work(board, "T-D", "release", "v9.9 发布", "data", "done",
          "target_version: v9.9\nevidence:\n  - type: document\n    source: dev\n"
          "    locator: docs/T-D-release.md\n    result: passed\n")
    (board / "docs/T-D-release.md").write_text(
        "# 发布记录\n- 发布完成，功能正常\n")
    # T-E: open item long past due → DEADLINE_BREACH
    _work(board, "T-E", "task", "逾期开放任务", "data", "doing",
          "due_on: \"2020-01-01\"\n")
    # T-F: done, only external URL evidence (unresolvable) → INCONCLUSIVE
    _work(board, "T-F", "task", "外部证据任务", "data", "done",
          "evidence:\n  - type: manual_check\n    source: dev\n"
          "    locator: https://external.example/report\n    result: passed\n")
    # T-G: breaking contract done without human ack → AUTHORIZATION_GAP/C-VIOLATION
    _work(board, "T-G", "task", "破坏性契约变更", "data", "done",
          "contract:\n  kind: http\n  status: live\n  breaking: true\n")

    # design: obligation 4 has no bearing item → TASK_COVERAGE_GAP
    (board / ".ousheng/designs/calib/design.md").write_text(
        "---\nstatus: agreed\nowner: data\nsystems:\n    - data\n"
        "related_items:\n    - T-A\n    - T-B\n    - T-C\n"
        "decided_by: pm\ndecided_at: \"2026-01-01T00:00:00+08:00\"\n---\n\n"
        "# 校准设计\n## 义务\n1. 功能A 交付并有验收记录\n2. 功能B 交付并有证据\n"
        "3. 功能C 交付并有可解析证据\n4. 功能D 交付（无任何承接任务）\n")

    _git(board, "init", "-q")
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "calibration board")

    suite = root
    (suite / "expected.json").write_text(
        json.dumps(TRUTH, ensure_ascii=False, indent=2))
    return suite
