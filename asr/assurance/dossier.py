"""Dossier assembly — the assessor's complete, pinned input.

MODOU-the-rope does the deterministic plumbing (snapshot pages, evidence
resolution, size caps); every semantic call stays with the LLM assessor.

Pages are addressed by stable ids (``baseline/...``, ``tasks/<ID>``,
``evidence/<ID>/<n>`` …) — findings must cite these ids, which is what the
artifact-citation gate later verifies mechanically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from asr.adapters.ousheng.snapshot import (
    BoardSnapshot, DesignRecord, EvidenceEntry, WorkItem, _git,
)

PAGE_CAP_BYTES = 8 * 1024  # per-page content cap; truncation is noted in-page
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
PROTOCOL_VERSION = "item-v1+design-v1"


def _cap(text: str) -> str:
    if len(text.encode()) <= PAGE_CAP_BYTES:
        return text
    kept = text.encode()[:PAGE_CAP_BYTES].decode(errors="ignore")
    return kept + "\n…[truncated at 8KB]"


@dataclass
class Dossier:
    kind: str                       # "design" | "item"
    subject: str                    # design topic or item id
    pages: dict[str, str] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def render(self) -> str:
        """Render as the assessor prompt body: every page fenced, id-labelled."""
        out = []
        for pid, content in self.pages.items():
            out.append(f"===== PAGE {pid} =====\n```\n{content}\n```")
        return "\n\n".join(out)


def _resolve_locator(snapshot: BoardSnapshot, ev: EvidenceEntry) -> tuple[str, str]:
    """Resolve an evidence locator against the pinned board snapshot.

    Returns (status, content): status ∈ RESOLVED | UNRESOLVED.
    Only board-internal paths are resolvable at P1; git commits need a
    code-repo mapping (P2), external URLs stay unresolved — honestly noted.
    """
    loc = ev.locator.strip()
    if not loc:
        return "UNRESOLVED", "empty locator"
    if _SHA_RE.match(loc):
        return "UNRESOLVED", (
            f"git commit {loc}: existence check requires code-repo mapping "
            f"(system→repo), not available in this run")
    if loc.startswith(("http://", "https://", "ssh://")):
        return "UNRESOLVED", f"external locator not fetched: {loc}"
    # Board-internal path?
    proc = _git(snapshot.board_dir, "cat-file", "-e", f"{snapshot.rev}:{loc}", check=False)
    if proc.returncode == 0:
        try:
            content = _git(snapshot.board_dir, "show", f"{snapshot.rev}:{loc}").stdout
            return "RESOLVED", _cap(content)
        except Exception as e:  # pragma: no cover — defensive
            return "UNRESOLVED", f"read failed: {e}"
    return "UNRESOLVED", f"locator not found in board tree at {snapshot.rev}: {loc}"


def _item_page(item: WorkItem) -> str:
    d = {
        "id": item.id, "type": item.type, "title": item.title,
        "system": item.system, "status": item.status, "revision": item.revision,
        "assignee": item.assignee, "acting_role": item.acting_role,
        "target_version": item.target_version, "priority": item.priority,
        "due_on": item.due_on, "depends_on": item.depends_on,
        "related_to": item.related_to, "contract": item.contract or None,
        "description": item.description or None,
        "evidence": [
            {"n": i + 1, "type": e.type, "source": e.source, "locator": e.locator,
             "result": e.result, "observed_at": e.observed_at, "note": e.note}
            for i, e in enumerate(item.evidence)
        ] or None,
    }
    return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)


def _evidence_pages(snapshot: BoardSnapshot, item: WorkItem, pages: dict[str, str]) -> None:
    for i, ev in enumerate(item.evidence, start=1):
        status, content = _resolve_locator(snapshot, ev)
        pages[f"evidence/{item.id}/{i}"] = (
            f"type: {ev.type}\nlocator: {ev.locator}\nresult: {ev.result}\n"
            f"resolution: {status}\n--- content ---\n{content}")


def _context_page(snapshot: BoardSnapshot, subject: str, kind: str, rules_ref: str | None) -> str:
    return yaml.safe_dump({
        "as_of_commit": snapshot.rev,
        "board_project": snapshot.project.get("id"),
        "subject": subject, "subject_kind": kind,
        "protocol_version": PROTOCOL_VERSION,
        "rules_ref": rules_ref,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "caps": {"page_bytes": PAGE_CAP_BYTES},
    }, allow_unicode=True, sort_keys=False)


def build_design_dossier(snapshot: BoardSnapshot, design: DesignRecord,
                         rules_text: str | None = None,
                         rules_ref: str | None = None) -> Dossier:
    """Assemble the two-level dossier for one decided design."""
    d = Dossier(kind="design", subject=design.topic)
    d.meta = {"design": design.topic, "rev": snapshot.rev,
              "items": [i for i in design.related_items if i in snapshot.work_items],
              "dangling_related": [i for i in design.related_items
                                   if i not in snapshot.work_items]}

    d.pages[f"baseline/{design.topic}/design.md"] = _cap(design.body)
    d.pages[f"baseline/{design.topic}/frontmatter"] = yaml.safe_dump(
        design.frontmatter_raw, allow_unicode=True, sort_keys=False)

    seam: dict[str, dict] = {}
    for iid in d.meta["items"]:
        item = snapshot.work_items[iid]
        d.pages[f"tasks/{iid}"] = _cap(_item_page(item))
        _evidence_pages(snapshot, item, d.pages)
        if item.contract:
            seam[iid] = {"system": item.system, "contract": item.contract,
                         "status": item.status, "evidence_count": len(item.evidence)}
    if seam:
        d.pages["seam/contracts"] = yaml.safe_dump(seam, allow_unicode=True, sort_keys=False)
    if rules_text:
        d.pages["rules/ousheng"] = _cap(rules_text)
    d.pages["context"] = _context_page(snapshot, design.topic, "design", rules_ref)
    return d


def build_item_dossier(snapshot: BoardSnapshot, item: WorkItem,
                       rules_text: str | None = None,
                       rules_ref: str | None = None) -> Dossier:
    """Assemble a single-item dossier (T1 sampled / reassessment)."""
    d = Dossier(kind="item", subject=item.id)
    d.meta = {"item": item.id, "rev": snapshot.rev}
    d.pages[f"tasks/{item.id}"] = _cap(_item_page(item))
    _evidence_pages(snapshot, item, d.pages)
    # Baseline hint: the item's own textual design anchor, if declared
    anchor = next((t for t in snapshot.decided_designs
                   if f"design:{t}" in (item.title + item.description)), None)
    if anchor:
        d.pages[f"baseline/{anchor}/design.md"] = _cap(snapshot.designs[anchor].body)
    if rules_text:
        d.pages["rules/ousheng"] = _cap(rules_text)
    d.pages["context"] = _context_page(snapshot, item.id, "item", rules_ref)
    return d
