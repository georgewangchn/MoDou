"""Organization-runtime-neutral board data model (anti-corruption core).

These dataclasses describe what the assurance core needs to know about a
task board — work items, designs, evidence, cross-system seams — with zero
vocabulary from any specific organization runtime. Adapters own the parsing
and schema validation of their concrete formats and produce these neutral
models;
the core (grading / dossier / pipeline) imports from here, never from an
adapter. A second organization runtime integrates by shipping its own
adapter that emits this model — the core stays untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Design statuses that count as "decided" (an assurance-relevant baseline
# state; the concrete vocabulary happens to be shared by decision runtimes).
DESIGN_DECIDED_STATUSES = {"agreed", "decided"}


@dataclass
class EvidenceEntry:
    type: str = ""
    source: str = ""
    locator: str = ""
    result: str = ""
    observed_at: str = ""
    note: str = ""


@dataclass
class WorkItem:
    id: str
    path: str
    schema_version: int = 0
    type: str = ""
    title: str = ""
    system: str = ""
    target_version: str = ""
    priority: str = ""
    due_on: str = ""
    status: str = ""
    revision: int = 0
    assignee: str = ""
    acting_role: str = ""
    accountable_human: str = ""
    detected_by: str = ""
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)
    contract: dict = field(default_factory=dict)
    human_ack: dict | None = None
    evidence: list[EvidenceEntry] = field(default_factory=list)


@dataclass
class DesignRecord:
    topic: str
    path: str
    status: str = ""
    owner: str = ""
    systems: list[str] = field(default_factory=list)
    related_items: list[str] = field(default_factory=list)
    decided_by: str = ""
    decided_at: str = ""
    body: str = ""
    frontmatter_raw: dict = field(default_factory=dict)


@dataclass
class BoardSnapshot:
    board_dir: Path
    rev: str
    project: dict = field(default_factory=dict)
    systems: dict = field(default_factory=dict)
    roles: dict = field(default_factory=dict)
    work_items: dict[str, WorkItem] = field(default_factory=dict)
    designs: dict[str, DesignRecord] = field(default_factory=dict)

    @property
    def decided_designs(self) -> dict[str, DesignRecord]:
        return {
            t: d for t, d in self.designs.items()
            if d.status in DESIGN_DECIDED_STATUSES
        }

    def t0_closure(self) -> set[str]:
        """Item ids covered by decided designs' structured related_items."""
        ids: set[str] = set()
        for d in self.decided_designs.values():
            ids.update(d.related_items)
        return {i for i in ids if i in self.work_items}
