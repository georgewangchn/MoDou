from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock

from asr.events.models import AgentName, Event, EventType, event_from_dict


class EventStore:
    def __init__(self, event_dir: str | Path = ".runtime/events",
                 inbox_dir: str | Path = ".runtime/inbox",
                 patches_dir: str | Path = ".runtime/patches",
                 diffs_dir: str | Path = ".runtime/diffs",
                 state_dir: str | Path = ".runtime/state",
                 tasks_dir: str | Path = ".runtime/tasks"):
        self._event_dir = Path(event_dir)
        self._event_dir.mkdir(parents=True, exist_ok=True)
        self._inbox_dir = Path(inbox_dir)
        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._patches_dir = Path(patches_dir)
        self._patches_dir.mkdir(parents=True, exist_ok=True)
        self._diffs_dir = Path(diffs_dir)
        self._diffs_dir.mkdir(parents=True, exist_ok=True)
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._tasks_dir = Path(tasks_dir)
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def write_event(self, event: Event) -> str:
        event_path = self._event_dir / f"{event.event_id}.json"
        lock_path = self._event_dir / f"{event.event_id}.lock"
        lock = FileLock(str(lock_path), timeout=10, lifetime=300)

        with lock:
            tmp_path = self._event_dir / f"{event.event_id}.tmp"
            tmp_path.write_text(event.model_dump_json(indent=2))
            tmp_path.rename(event_path)

        if event.type == EventType.CONVERGED or event.type == EventType.STUCK:
            self._save_state(event.task_id, event)

        return str(event_path)

    def read_event(self, event_id: str) -> Event:
        event_path = self._event_dir / f"{event_id}.json"
        data = json.loads(event_path.read_text())
        return event_from_dict(data)

    def poll_inbox(self, agent: AgentName) -> list[Event]:
        inbox_dir = self._inbox_dir / str(agent.value)
        if not inbox_dir.exists():
            return []
        events = []
        for f in sorted(inbox_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                events.append(event_from_dict(data))
            except Exception:
                continue
        events.sort(key=lambda e: (e.timestamp, e.sequence))
        return events

    def get_task_events(self, task_id: str) -> list[Event]:
        events = []
        for f in sorted(self._event_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                if data.get("task_id") == task_id:
                    events.append(event_from_dict(data))
            except Exception:
                continue
        events.sort(key=lambda e: (e.timestamp, e.sequence))
        return events

    def replay_events(self, task_id: str) -> list[Event]:
        return self.get_task_events(task_id)

    def get_causal_chain(self, event_id: str) -> list[Event]:
        """P0-1: Trace the causal chain backwards from a given event.

        Follows caused_by links to reconstruct the full causal ancestry.
        Returns events in reverse-chronological order (target first, root last).
        """
        chain: list[Event] = []
        visited: set[str] = set()
        current_id: str | None = event_id
        while current_id and current_id not in visited:
            visited.add(current_id)
            try:
                evt = self.read_event(current_id)
                chain.append(evt)
                current_id = evt.caused_by
            except (FileNotFoundError, json.JSONDecodeError):
                break
        return chain

    def get_superseded_chain(self, event_id: str) -> list[Event]:
        """P0-1: Trace the supersession chain forward from a given event.

        Finds all events that supersede the given event, in order.
        Useful for tracking how a decision evolved over iterations.
        """
        task_events = self.get_task_events(self.read_event(event_id).task_id)
        chain: list[Event] = []
        current_id: str | None = event_id
        while current_id:
            next_supersede: str | None = None
            for evt in task_events:
                if evt.supersedes == current_id:
                    chain.append(evt)
                    next_supersede = evt.event_id
                    break
            current_id = next_supersede
        return chain

    def get_events_by_type(self, task_id: str, event_type: EventType) -> list[Event]:
        """P0-1: Filter events by type for a given task."""
        return [e for e in self.get_task_events(task_id) if e.type == event_type]

    def get_valid_events(self, task_id: str) -> list[Event]:
        """P0-1: Return only events that have not been superseded (validity_until is None)."""
        return [e for e in self.get_task_events(task_id) if e.validity_until is None]

    def mark_superseded(self, event_id: str, by_event_id: str) -> None:
        """P0-1: Mark an event as superseded by closing its validity interval."""
        event_path = self._event_dir / f"{event_id}.json"
        if not event_path.exists():
            return
        data = json.loads(event_path.read_text())
        data["validity_until"] = datetime.now(UTC).isoformat()
        event_path.write_text(json.dumps(data, indent=2, default=str))

    def save_patch(self, task_id: str, diff_text: str, file_path: str) -> str:
        patch_path = self._patches_dir / f"{task_id}_{file_path.replace('/', '_')}.patch"
        lock = FileLock(str(patch_path) + ".lock", timeout=10, lifetime=300)
        with lock:
            patch_path.write_text(diff_text)
        return str(patch_path)

    def save_diff(self, task_id: str, diff_text: str) -> str:
        diff_path = self._diffs_dir / f"{task_id}.diff"
        diff_path.write_text(diff_text)
        return str(diff_path)

    def save_task_state(self, task_id: str, state: dict) -> str:
        state_path = self._tasks_dir / f"{task_id}.json"
        state_path.write_text(json.dumps(state, indent=2))
        return str(state_path)

    def _save_state(self, task_id: str, event: Event) -> str:
        state_path = self._state_dir / f"{task_id}.json"
        state_path.write_text(event.model_dump_json(indent=2))
        return str(state_path)
