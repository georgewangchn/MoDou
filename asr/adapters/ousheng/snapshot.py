"""OuSheng board adapter — snapshot reader (audit channel).

Reads a fixed git revision of an OuSheng board workspace via
``git show <rev>:<path>`` so assessments are pinned to an immutable
snapshot and never observe a writer's intermediate state.

Fail-loud discipline: unknown schema versions, YAML parse errors or
missing structural files raise :class:`SnapshotError` (the CLI maps it
to ``BLOCKED``, exit code 2). A single bad file blocks the audit rather
than silently vanishing from it.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Board layout (OuSheng v0.3 runtime, schema_version=2 work items)
WORK_DIR = ".ousheng/work"
DESIGN_DIR = ".ousheng/designs"
ACTORS_DIR = ".ousheng/actors"

SUPPORTED_WORK_SCHEMA = 2
DESIGN_DECIDED_STATUSES = {"agreed", "decided"}


class SnapshotError(Exception):
    """Raised when the board snapshot cannot be loaded fail-loud."""


def _git(board_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(board_dir), *args],
        capture_output=True,
        text=True,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if check and proc.returncode != 0:
        raise SnapshotError(f"git {' '.join(args[:2])}… failed: {proc.stderr.strip()[:300]}")
    return proc


def _list_files(board_dir: Path, rev: str, tree: str) -> list[str]:
    out = _git(board_dir, "ls-tree", "-r", "--name-only", rev, "--", tree).stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


def _read_file(board_dir: Path, rev: str, path: str) -> str:
    return _git(board_dir, "show", f"{rev}:{path}").stdout


def _load_yaml(text: str, path: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SnapshotError(f"YAML parse error in {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SnapshotError(f"{path}: expected mapping, got {type(data).__name__}")
    return data


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


def _parse_work_item(path: str, data: dict) -> WorkItem:
    item = WorkItem(id=str(data.get("id") or Path(path).stem), path=path)
    item.schema_version = int(data.get("schema_version") or 0)
    if item.schema_version != SUPPORTED_WORK_SCHEMA:
        raise SnapshotError(
            f"{path}: unsupported schema_version={item.schema_version} "
            f"(supported: {SUPPORTED_WORK_SCHEMA})")
    item.type = str(data.get("type") or "")
    item.title = str(data.get("title") or "")
    item.system = str(data.get("system") or "")
    item.target_version = str(data.get("target_version") or "")
    item.priority = str(data.get("priority") or "")
    item.due_on = str(data.get("due_on") or "")
    item.status = str(data.get("status") or "")
    item.revision = int(data.get("revision") or 0)
    item.assignee = str(data.get("assignee") or "")
    item.acting_role = str(data.get("acting_role") or "")
    item.accountable_human = str(data.get("accountable_human") or "")
    item.detected_by = str(data.get("detected_by") or "")
    desc = data.get("description")
    item.description = str(desc) if desc else ""
    item.depends_on = [str(x) for x in (data.get("depends_on") or [])]
    item.related_to = [str(x) for x in (data.get("related_to") or [])]
    contract = data.get("contract")
    item.contract = dict(contract) if isinstance(contract, dict) else {}
    ack = data.get("human_ack")
    item.human_ack = dict(ack) if isinstance(ack, dict) else None
    for ev in data.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        item.evidence.append(EvidenceEntry(
            type=str(ev.get("type") or ""),
            source=str(ev.get("source") or ""),
            locator=str(ev.get("locator") or ""),
            result=str(ev.get("result") or ""),
            observed_at=str(ev.get("observed_at") or ""),
            note=str(ev.get("note") or ""),
        ))
    return item


def _split_design_doc(text: str) -> tuple[dict, str]:
    """Split a design doc into (frontmatter, body)."""
    if not text.startswith("---"):
        return {}, text
    rest = text[3:]  # drop the leading '---'
    if "\n---" not in rest:
        return {}, text
    fm_text, body = rest.split("\n---", 1)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), body.lstrip("\n")


def _parse_design(topic: str, path: str, text: str) -> DesignRecord:
    fm, body = _split_design_doc(text)
    d = DesignRecord(topic=topic, path=path, body=body, frontmatter_raw=fm)
    d.status = str(fm.get("status") or "")
    d.owner = str(fm.get("owner") or "")
    d.systems = [str(s) for s in (fm.get("systems") or [])]
    d.related_items = [str(i) for i in (fm.get("related_items") or [])]
    d.decided_by = str(fm.get("decided_by") or "")
    d.decided_at = str(fm.get("decided_at") or "")
    return d


def load_snapshot(board_dir: Path | str, rev: str = "HEAD") -> BoardSnapshot:
    """Load a pinned board snapshot. Raises SnapshotError on any structural problem."""
    board_dir = Path(board_dir)
    if not board_dir.exists():
        raise SnapshotError(f"board dir not found: {board_dir}")

    snap = BoardSnapshot(board_dir=board_dir, rev=rev)

    # Structural files — project/systems required, roles best-effort
    for name, required in (("project.yaml", True), ("systems.yaml", True), ("roles.yaml", False)):
        path = f".ousheng/{name}"
        try:
            data = _load_yaml(_read_file(board_dir, rev, path), path)
        except SnapshotError:
            if required:
                raise
            data = {}
        if name == "project.yaml":
            snap.project = data
        elif name == "systems.yaml":
            snap.systems = data
        else:
            snap.roles = data

    # Work items — strict
    for path in _list_files(board_dir, rev, WORK_DIR):
        if not path.endswith(".yaml"):
            continue
        data = _load_yaml(_read_file(board_dir, rev, path), path)
        item = _parse_work_item(path, data)
        snap.work_items[item.id] = item

    # Designs — strict frontmatter, tolerant body
    for path in _list_files(board_dir, rev, DESIGN_DIR):
        if not path.endswith("/design.md"):
            continue
        topic = path[len(DESIGN_DIR) + 1:-len("/design.md")]
        snap.designs[topic] = _parse_design(topic, path, _read_file(board_dir, rev, path))

    if not snap.work_items:
        raise SnapshotError(f"no work items found under {WORK_DIR}/ at {rev}")
    return snap
