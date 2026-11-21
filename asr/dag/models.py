from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CONVERGED = "converged"
    STUCK = "stuck"
    SKIPPED = "skipped"
    NEEDS_RERUN = "needs_rerun"  # P0-2: completed node needs to re-run due to dynamic edge injection


@dataclass
class TaskNode:
    id: str
    name: str
    description: str
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    iterations: int = 0
    patch_count: int = 0
    errors_resolved: int = 0
    # P0-2: Track dynamic edge injections for observability
    injected_deps: list[tuple[str, str]] = field(default_factory=list)  # (from_node, reason)
    rerun_count: int = 0  # How many times this node has been re-queued

    def is_ready(self, completed: set[str]) -> bool:
        return all(dep in completed for dep in self.depends_on)

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass
class DAGResult:
    total_nodes: int
    converged: int
    stuck: int
    skipped: int
    total_iterations: int
    node_results: dict[str, NodeStatus] = field(default_factory=dict)


class TaskDAG:
    def __init__(self, task_id: str, project_dir: Path):
        self.task_id = task_id
        self.project_dir = project_dir
        self.nodes: dict[str, TaskNode] = {}
        self._completed: set[str] = set()

    def add_node(self, node: TaskNode) -> None:
        self.nodes[node.id] = node

    def get_ready_nodes(self) -> list[TaskNode]:
        return [
            n for n in self.nodes.values()
            if (n.status == NodeStatus.PENDING or n.status == NodeStatus.NEEDS_RERUN)
            and n.is_ready(self._completed)
        ]

    def mark_completed(self, node_id: str, status: NodeStatus) -> None:
        if node_id in self.nodes:
            self.nodes[node_id].status = status
            if status in (NodeStatus.CONVERGED, NodeStatus.STUCK, NodeStatus.SKIPPED):
                self._completed.add(node_id)

    def inject_dependency(self, from_node: str, to_node: str, reason: str = "") -> bool:
        """P0-2: Dynamically inject a dependency edge at runtime.

        When Builder modifies files in node A that affect node B, this method
        adds B as a dependency of A (B must re-run after A completes).

        Returns True if the edge was injected, False if it already existed
        or either node doesn't exist.
        """
        if from_node not in self.nodes or to_node not in self.nodes:
            return False
        node = self.nodes[from_node]
        if to_node in node.depends_on:
            return False
        node.depends_on.append(to_node)
        node.injected_deps.append((to_node, reason))
        return True

    def requeue_node(self, node_id: str) -> bool:
        """P0-2: Re-queue a completed node for re-execution.

        This is needed when a dynamic dependency injection makes a previously
        completed node need re-running (e.g., its upstream dependency changed).

        Returns True if the node was re-queued, False if it doesn't exist
        or is currently running.
        """
        if node_id not in self.nodes:
            return False
        node = self.nodes[node_id]
        if node.status == NodeStatus.RUNNING:
            return False
        # Remove from completed set so it can be picked up again
        self._completed.discard(node_id)
        node.status = NodeStatus.NEEDS_RERUN
        node.rerun_count += 1
        return True

    def get_rerun_nodes(self) -> list[TaskNode]:
        """P0-2: Get all nodes that need re-running due to dynamic edge injection."""
        return [
            n for n in self.nodes.values()
            if n.status == NodeStatus.NEEDS_RERUN
        ]

    def topological_order(self) -> list[TaskNode]:
        visited: set[str] = set()
        order: list[TaskNode] = []

        def visit(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            node = self.nodes.get(node_id)
            if node:
                for dep in node.depends_on:
                    visit(dep)
                order.append(node)

        for nid in list(self.nodes.keys()):
            visit(nid)

        return order

    def all_done(self) -> bool:
        return all(
            n.status in (NodeStatus.CONVERGED, NodeStatus.STUCK, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )

    def result(self) -> DAGResult:
        converged = sum(1 for n in self.nodes.values() if n.status == NodeStatus.CONVERGED)
        stuck = sum(1 for n in self.nodes.values() if n.status == NodeStatus.STUCK)
        skipped = sum(1 for n in self.nodes.values() if n.status == NodeStatus.SKIPPED)
        iterations = sum(n.iterations for n in self.nodes.values())
        return DAGResult(
            total_nodes=len(self.nodes),
            converged=converged,
            stuck=stuck,
            skipped=skipped,
            total_iterations=iterations,
            node_results={nid: n.status for nid, n in self.nodes.items()},
        )
