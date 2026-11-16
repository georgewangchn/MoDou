"""P2-1: Persistent Domain Memory — cross-task knowledge accumulation.

Control theory mapping: feedforward control enhancement.
Builder session resets cause 'amnesia' — P0-2's _build_builder_context only
injects current-task state. DomainMemory persists across tasks, accumulating:
- Successful repair strategies
- Known failure patterns
- Project-specific constraints
- Effective modification approaches

This gives Builder a 'long-term memory' that survives session resets and
crosses task boundaries — similar to Graph Engineering's persistent agent memory.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


class DomainMemory:
    """Manages a persistent memory file for a specific agent zone.

    Memory file format (Markdown):
        # Domain Memory: {zone}
        ## Project Constraints
        - ...
        ## Known Patterns
        - ...
        ## Recent Strategies (last 10)
        - [iter N] strategy → outcome
    """

    MAX_ENTRIES = 50  # Keep memory bounded — evict oldest entries
    MAX_FILE_SIZE = 4096  # 4KB cap to avoid context bloat

    def __init__(self, zone: str, memory_dir: Path):
        self._zone = zone
        self._dir = memory_dir
        self._path = memory_dir / f"{zone}_builder.md"

    def load(self) -> str:
        """Load domain memory for injection into Builder prompt.

        Returns empty string if no memory exists or file is too large.
        """
        if not self._path.exists():
            return ""
        try:
            content = self._path.read_text()
        except (OSError, UnicodeDecodeError):
            return ""
        if len(content) > self.MAX_FILE_SIZE:
            # Truncate to last MAX_FILE_SIZE chars (most recent entries)
            content = content[-self.MAX_FILE_SIZE:]
            # Find the first newline to avoid cutting mid-line
            nl = content.find("\n")
            if nl > 0:
                content = content[nl + 1:]
        return content

    def update(self, iteration: int, metrics: dict[str, Any],
               repair_mode: str, trend: str,
               failures: list[dict] | None = None,
               feedback: list[str] | None = None,
               patch_applied: bool = False) -> None:
        """Update domain memory based on iteration results.

        Records:
        - Successful repair strategies (pass_rate improved)
        - Failed approaches (pass_rate regressed)
        - New project constraints discovered
        - Recurring failure patterns
        """
        self._dir.mkdir(parents=True, exist_ok=True)

        entries: list[str] = []

        # Determine outcome
        pass_rate = metrics.get("test_pass_rate", 0.0)
        failed_count = metrics.get("test_failed_count", 0)

        if patch_applied:
            if trend == "improving":
                # Record successful strategy
                strategy = self._extract_strategy(feedback, repair_mode)
                entries.append(
                    f"- [iter {iteration}] ✓ {repair_mode}: {strategy} → "
                    f"pass_rate {pass_rate:.0%} ({failed_count} failures)"
                )
            elif trend == "regressing":
                # Record failed approach
                strategy = self._extract_strategy(feedback, repair_mode)
                entries.append(
                    f"- [iter {iteration}] ✗ {repair_mode}: {strategy} → "
                    f"REGRESSED pass_rate {pass_rate:.0%}"
                )

        # Record recurring failures
        if failures and len(failures) > 0:
            nodeids = [f.get("nodeid", "?") for f in failures[:3]]
            entries.append(
                f"- [iter {iteration}] failures: {', '.join(nodeids)}"
            )

        # Extract project constraints from Analyzer feedback
        if feedback:
            for fb in feedback:
                if "[CONSTRAINT]" in fb or "[MISSING]" in fb:
                    # Extract the constraint text
                    constraint = self._extract_constraint(fb)
                    if constraint:
                        entries.append(f"- [constraint] {constraint}")

        if not entries:
            return

        # Read existing memory
        existing = ""
        if self._path.exists():
            try:
                existing = self._path.read_text()
            except (OSError, UnicodeDecodeError):
                existing = ""

        # Append new entries to "Recent Strategies" section
        timestamp = time.strftime("%Y-%m-%d %H:%M")
        new_block = f"\n<!-- {timestamp} iter {iteration} -->\n" + "\n".join(entries)

        # Simple append-based update with section management
        if "## Recent Strategies" in existing:
            # Insert after the header
            parts = existing.split("## Recent Strategies", 1)
            updated = parts[0] + "## Recent Strategies" + new_block + parts[1]
        else:
            # Create the section
            header = f"# Domain Memory: {self._zone}\n\n## Project Constraints\n\n## Recent Strategies\n"
            updated = header + new_block + "\n"

        # Enforce entry limit by keeping only last MAX_ENTRIES lines starting with "- ["
        lines = updated.split("\n")
        entry_lines = [(i, l) for i, l in enumerate(lines) if l.strip().startswith("- [iter")]
        if len(entry_lines) > self.MAX_ENTRIES:
            # Remove oldest entries
            to_remove = set(idx for idx, _ in entry_lines[:-self.MAX_ENTRIES])
            lines = [l for i, l in enumerate(lines) if i not in to_remove]
            updated = "\n".join(lines)

        # Enforce file size limit
        if len(updated) > self.MAX_FILE_SIZE:
            # Keep the header + most recent entries
            lines = updated.split("\n")
            header_end = 0
            for i, l in enumerate(lines):
                if "## Recent Strategies" in l:
                    header_end = i + 1
                    break
            recent_lines = [l for l in lines[header_end:] if l.strip().startswith("- [") or l.strip().startswith("<!--")]
            # Keep last entries that fit
            fitting = []
            size = len("\n".join(lines[:header_end]))
            for l in reversed(recent_lines):
                if size + len(l) + 1 > self.MAX_FILE_SIZE:
                    break
                fitting.insert(0, l)
                size += len(l) + 1
            updated = "\n".join(lines[:header_end]) + "\n" + "\n".join(fitting)

        try:
            self._path.write_text(updated)
        except OSError:
            pass  # Non-critical — memory is best-effort

    def _extract_strategy(self, feedback: list[str] | None, repair_mode: str) -> str:
        """Extract a concise strategy description from feedback."""
        if not feedback:
            return repair_mode
        # Look for mode-specific instructions
        for fb in feedback:
            if fb.startswith("[MODE:"):
                # Extract the mode instruction
                end = fb.find("]")
                if end > 0:
                    return fb[1:end]
        # Look for priority items
        for fb in feedback:
            if fb.startswith("[PRIORITY]"):
                return fb[:80]
        # Fallback: first feedback item
        return feedback[0][:80] if feedback else repair_mode

    def _extract_constraint(self, feedback_line: str) -> str:
        """Extract constraint text from a feedback line."""
        # Remove tags like [CONSTRAINT], [MISSING]
        cleaned = re.sub(r'\[CONSTRAINT\]|\[MISSING\]', '', feedback_line).strip()
        # Take first sentence
        sentence_end = cleaned.find('.')
        if sentence_end > 0 and sentence_end < 120:
            return cleaned[:sentence_end + 1]
        return cleaned[:120] if cleaned else ""

    def clear(self) -> None:
        """Clear all domain memory."""
        if self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass
