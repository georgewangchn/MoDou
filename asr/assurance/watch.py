"""Board watch — unattended recurring shadow assessment (P4).

One cycle = fetch (pin sha) → snapshot → grade → assess (designs + sample)
→ gates → store → append cycle summary to ``trend.jsonl``.

Robustness for unattended runs: every cycle is fully isolated — snapshot
drift, transport failures and LLM outages are recorded and the loop
continues (BLOCKED is a cycle outcome, never a crash). Cruise treats the
exit codes as observation only (0/1/2 = verdict/inconclusive-or-deviation/
environment — none of these mean "execution failed").
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from asr.assurance.compose import GateConfig
from asr.assurance.pipeline import run_board_assessment
from asr.assurance.store import AssessmentStore


@dataclass
class CycleSummary:
    cycle: int
    started_at: str
    board: str
    rev: str
    statuses: dict[str, str] = field(default_factory=dict)   # subject → status
    new_fingerprints: list[str] = field(default_factory=list)
    recurred_fingerprints: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def fetch_and_resolve(board_dir: Path) -> str:
    """git fetch + resolve FETCH_HEAD to a pinned sha. Raises on no remote."""
    proc = subprocess.run(
        ["git", "-C", str(board_dir), "fetch", "origin"],
        capture_output=True, text=True,
        env={"GIT_TERMINAL_PROMPT": "0",
             "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    if proc.returncode != 0:
        raise RuntimeError(f"git fetch failed: {proc.stderr.strip()[:200]}")
    proc = subprocess.run(
        ["git", "-C", str(board_dir), "rev-parse", "FETCH_HEAD"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    if proc.returncode != 0:
        raise RuntimeError(f"rev-parse FETCH_HEAD failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def _prior_fingerprints(store: AssessmentStore) -> set[str]:
    """All fingerprints recorded in the store BEFORE this cycle writes.

    Must be called before run_board_assessment — afterwards the current
    cycle's own assessments would pollute the baseline.
    """
    fps: set[str] = set()
    for e in store.entries():
        data = store.load(e["assessment_id"])
        if not data:
            continue
        for f in data.get("findings", []):
            if f.get("fingerprint"):
                fps.add(f["fingerprint"])
    return fps


async def run_cycle(cycle: int, board_dir: Path, store: AssessmentStore,
                    fetch: bool = True, rev: str | None = None,
                    designs: list[str] | None = None,
                    sample_rate: float = 0.0,
                    rules_file: str | None = None,
                    rules_ref: str | None = None,
                    workdir: Path | None = None,
                    gate_cfg: GateConfig | None = None) -> CycleSummary:
    started = datetime.now(timezone.utc).isoformat()
    s = CycleSummary(cycle=cycle, started_at=started, board="", rev=rev or "HEAD")
    try:
        if fetch:
            s.rev = fetch_and_resolve(Path(board_dir))
        elif rev is None:
            s.rev = "HEAD"
        prior_fps = _prior_fingerprints(store)  # BEFORE this cycle writes

        report = await run_board_assessment(
            board_dir=board_dir, rev=s.rev, designs=designs,
            sample_rate=sample_rate, rules_file=rules_file,
            rules_ref=rules_ref, workdir=workdir, gate_cfg=gate_cfg,
            store=store)
        s.board = report.board
        s.errors = list(report.errors)
        current_fps: set[str] = set()
        for a in report.assessments:
            s.statuses[a.subject] = a.status
            for f in a.findings:
                if f.fingerprint:
                    current_fps.add(f.fingerprint)
        s.new_fingerprints = sorted(current_fps - prior_fps)
        s.recurred_fingerprints = sorted(current_fps & prior_fps)
    except Exception as e:  # unattended: record, never crash the loop
        s.errors.append(f"{type(e).__name__}: {str(e)[:300]}")
    # cycle log (append-only, MODOU-local)
    with (store.root / "trend.jsonl").open("a") as fh:
        fh.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    # last-run file for cruise/PM observation (non-blocking signal only)
    (store.root / "last-run.json").write_text(
        json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
    return s


async def watch_loop(board_dir: Path, store: AssessmentStore,
                     interval: float, **kwargs) -> None:
    cycle = 0
    while True:
        cycle += 1
        s = await run_cycle(cycle, board_dir, store, **kwargs)
        flag = " OK" if not s.errors else f" ERRORS={len(s.errors)}"
        print(f"[watch] cycle {s.cycle} rev={s.rev[:10]} "
              f"subjects={len(s.statuses)} new={len(s.new_fingerprints)} "
              f"recurred={len(s.recurred_fingerprints)}{flag}", flush=True)
        await asyncio.sleep(interval)


# ── trend aggregation ────────────────────────────────────────────────────────
def compute_trend(store: AssessmentStore) -> dict:
    """Latest-per-subject statuses, code histogram, recurrences, resolution.

    Explicit denominators: 'subjects' counts latest assessments per subject;
    'recurred' counts fingerprints seen in ≥2 assessments; 'resolved' counts
    fingerprints present in a subject's history but absent from its latest.
    """
    entries = store.entries()
    history: dict[str, list[dict]] = {}
    for e in entries:  # index is append-ordered
        history.setdefault(e["subject"], []).append(e)

    latest_status: dict[str, str] = {}
    code_hist: dict[str, int] = {}
    recurred: list[str] = []
    resolved: list[str] = []

    for subject, es in history.items():
        latest = es[-1]
        latest_status[subject] = latest["status"]
        latest_data = store.load(latest["assessment_id"]) or {}
        latest_fps = set()
        for f in latest_data.get("findings", []):
            code_hist[f["code"]] = code_hist.get(f["code"], 0) + 1
            if f.get("fingerprint"):
                latest_fps.add(f["fingerprint"])
        seen_count: dict[str, int] = {}
        for e in es:
            d = store.load(e["assessment_id"]) or {}
            for f in d.get("findings", []):
                fp = f.get("fingerprint")
                if fp:
                    seen_count[fp] = seen_count.get(fp, 0) + 1
        for fp, n in seen_count.items():
            if n >= 2 and fp in latest_fps:
                recurred.append(fp)
            elif fp not in latest_fps:
                resolved.append(fp)

    status_hist: dict[str, int] = {}
    for st in latest_status.values():
        status_hist[st] = status_hist.get(st, 0) + 1

    return {
        "subjects": len(history),
        "runs": len(entries),
        "status_histogram_latest": status_hist,
        "code_histogram_latest": code_hist,
        "recurred_fingerprints": sorted(recurred),
        "resolved_fingerprints": sorted(resolved),
        "denominators": {
            "subjects": "latest assessment per subject",
            "recurred": "fingerprint in >=2 assessments and still in latest",
            "resolved": "fingerprint in subject history, absent from latest",
        },
    }
