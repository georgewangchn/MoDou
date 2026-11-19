"""Feedback controller — plan and execute authorized writeback (P3).

Discipline (Final-1.0 §6.3 / §8):
- default dry-run; nothing reaches the board without --execute;
- only gated findings become correction items (severity floor, budget);
- fingerprint idempotency: an OPEN correction for the same fingerprint is
  never duplicated; PM-marked false positives are never written back;
- failures record PENDING and stop — never blind-retry a create (a timed-out
  create may have succeeded; the next run's dedup query resolves it);
- correction items start in backlog: PM triage IS the authorization step.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class WritebackPolicy:
    min_severity: str = "medium"      # low findings stay report-only by default
    max_items: int = 5                # per-run budget
    item_type: str = "bug"
    detected_by: str = "modou"
    id_prefix: str = "MD-"
    id_start: int = 900               # avoid colliding with live board ranges


@dataclass
class Correction:
    fingerprint: str
    code: str
    severity: str
    subject: str                      # work item id or design topic
    system: str
    title: str
    description: str
    assessment_id: str
    new_id: str | None = None         # filled after successful create
    state: str = "PLANNED"            # PLANNED|CREATED|SKIPPED|FAILED


def _desc(f: dict, assessment_id: str) -> str:
    keep = "\n".join(
        f"{k}: {str(f.get(k) or '')[:220]}"
        for k in ("expected", "actual", "recommendation"))
    refs = ",".join((f.get("evidence_refs") or [])[:6])
    return (f"[MODOU 纠偏建议] assessment={assessment_id} "
            f"fingerprint={f.get('fingerprint')}\nconfidence={f.get('confidence')} "
            f"refs={refs}\n{keep}")[:1500]


def plan_writeback(assessment: dict, policy: WritebackPolicy,
                   open_fingerprints: set[str],
                   fp_fingerprints: set[str]) -> list[Correction]:
    """Pure planning: filter → dedup → budget. No I/O, no MCP."""
    subject_system = assessment.get("scope", {}).get("subject_systems", {})
    out: list[Correction] = []
    for f in assessment.get("findings", []):
        fp = f.get("fingerprint")
        if not fp:
            continue
        if SEV_RANK.get(f["severity"], 9) > SEV_RANK[policy.min_severity]:
            continue
        if fp in open_fingerprints:
            continue  # idempotency: open correction exists
        if fp in fp_fingerprints:
            continue  # quality ledger says false positive — never write back
        subj = (f.get("subject") or {}).get("work_item") or assessment.get("subject", "")
        system = subject_system.get(subj) or subject_system.get("*") or ""
        if not system:
            continue  # cannot place the item without a system; stays report-only
        title = f"[MODOU] {subj} {f['code']}: {str(f.get('actual'))[:70]}"
        out.append(Correction(
            fingerprint=fp, code=f["code"], severity=f["severity"],
            subject=subj, system=system, title=title[:120],
            description=_desc(f, assessment["assessment_id"]),
            assessment_id=assessment["assessment_id"]))
        if len(out) >= policy.max_items:
            break
    return out


class CorrectionsLedger:
    """MODOU-local three-state ledger: OPEN → SUPERSEDED / CLOSED.

    State transitions are heuristic mappings from the board (see Final-1.0
    §8.3): correction item done/cancelled ⇒ CLOSED; superseded by a newer
    fingerprint for the same subject ⇒ SUPERSEDED.
    """

    def __init__(self, root: Path | str):
        self.path = Path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, correction: Correction, state: str, board: str = "",
               error: str = "") -> dict:
        entry = {"fingerprint": correction.fingerprint,
                 "correction_id": correction.new_id, "state": state,
                 "board": board, "assessment_id": correction.assessment_id,
                 "subject": correction.subject, "code": correction.code,
                 "error": error,
                 "at": datetime.now(timezone.utc).isoformat()}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def open_fingerprints(self) -> set[str]:
        return {e["fingerprint"] for e in self.entries() if e["state"] == "OPEN"}

    def next_id(self, policy: WritebackPolicy) -> str:
        used = {e["correction_id"] for e in self.entries() if e.get("correction_id")}
        n = policy.id_start
        while f"{policy.id_prefix}{n}" in used:
            n += 1
        return f"{policy.id_prefix}{n}"


def false_positive_fingerprints(ledger_path: Path | str) -> set[str]:
    """Fingerprints the PM quality ledger marked fp — writeback blacklist."""
    p = Path(ledger_path)
    if not p.exists():
        return set()
    return {json.loads(l)["fingerprint"] for l in p.read_text().splitlines()
            if l.strip() and json.loads(l).get("verdict") == "fp"}


def subject_systems_from_snapshot(snapshot) -> dict[str, str]:
    """item-id → system; design-topic → primary system (for placing corrections)."""
    m = {iid: it.system for iid, it in snapshot.work_items.items() if it.system}
    for topic, d in snapshot.designs.items():
        if d.systems:
            m.setdefault(topic, d.systems[0])
    return m


def _item_exists(client, item_id: str) -> bool:
    """Half-done recovery probe (Final-1.0 §8.4.5): a failed create may have
    succeeded server-side; query before declaring failure."""
    try:
        client.call_tool("get_work_item", {"path": client.board_dir, "id": item_id})
        return True
    except Exception:
        return False


def execute_writeback(corrections: list[Correction], client, actor: str,
                      ledger: CorrectionsLedger, board: str,
                      policy: WritebackPolicy, dry_run: bool = True) -> list[Correction]:
    """Create correction items via official MCP. dry-run only prints.

    Atomic unit = the create: once the item exists on the board the
    correction is OPEN (recorded immediately), even if the follow-up
    evidence pointer fails — that is recoverable, a duplicate create is not.
    Two-step per correction (OuSheng T10 — pointers only, no free-text
    injection surface): create_work_item carries a one-line title; the
    finding detail stays in MODOU's archive, referenced via an
    add_evidence(document) pointer on the new item.
    """
    for c in corrections:
        if dry_run:
            c.state = "PLANNED"
            continue
        c.new_id = ledger.next_id(policy)
        try:
            client.call_tool("create_work_item", {
                "path": client.board_dir, "id": c.new_id, "type": policy.item_type,
                "title": c.title, "system": c.system,
                "detected_by": policy.detected_by, "actor": actor,
            })
        except Exception as e:
            if not _item_exists(client, c.new_id):
                # genuinely failed — at-most-once, record and stop
                c.state = "FAILED"
                c.new_id = None
                ledger.append(c, "PENDING", board=board, error=str(e)[:300])
                continue
            # half-done recovery: item exists though create reported failure
        c.state = "CREATED"
        ledger.append(c, "OPEN", board=board)
        try:
            client.call_tool("add_evidence", {
                "path": client.board_dir, "work_id": c.new_id,
                "type": "document",
                "locator": f"modou-assessment://{c.assessment_id}#{c.fingerprint}",
                "source": "modou", "actor": actor,
                "note": f"{c.code}@{c.subject} sev={c.severity}",
                "expect": 1,  # fresh item: OuSheng creates at revision 1
            })
        except Exception as e:
            # item exists; only the pointer failed — record, do not recreate
            ledger.append(c, "OPEN", board=board,
                          error=f"evidence-pointer pending: {str(e)[:200]}")
    return corrections
