"""Assessment archive — MODOU-owned storage, never on the board.

Files: ``<root>/<ASM-id>.json`` + append-only ``index.jsonl``.
Reassessment linkage is carried inside the assessment itself
(``reassessment_of``); the index supports "latest for subject" lookups
used by writeback dedup and the fingerprint idempotency gate (P3).
"""
from __future__ import annotations

import json
from pathlib import Path


class AssessmentStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "index.jsonl"

    def save(self, assessment) -> Path:
        data = assessment.to_dict()
        path = self.root / f"{assessment.assessment_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        with self._index.open("a") as f:
            f.write(json.dumps({
                "assessment_id": assessment.assessment_id,
                "kind": assessment.kind,
                "subject": assessment.subject,
                "status": assessment.status,
                "as_of_commit": assessment.scope.get("as_of_commit"),
                "reassessment_of": assessment.reassessment_of,
            }, ensure_ascii=False) + "\n")
        return path

    def entries(self) -> list[dict]:
        if not self._index.exists():
            return []
        out = []
        for line in self._index.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def load(self, assessment_id: str) -> dict | None:
        path = self.root / f"{assessment_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def latest_for(self, kind: str, subject: str) -> dict | None:
        for e in reversed(self.entries()):
            if e.get("kind") == kind and e.get("subject") == subject:
                return e
        return None
