from __future__ import annotations

import asyncio
from pathlib import Path

from asr.agents.base import BaseAgent
from asr.config.models import ASRConfig
from asr.controller.convergence import ASRController, ConvergenceState
from asr.dag.models import DAGResult, NodeStatus, TaskDAG, TaskNode
from asr.events.store import EventStore
from asr.spec.models import Specification


class DAGExecutor:
    def __init__(
        self,
        config: ASRConfig,
        event_store: EventStore,
        project_dir: Path,
        builder: BaseAgent | None = None,
        tester: BaseAgent | None = None,
        analyzer: BaseAgent | None = None,
    ):
        self._config = config
        self._event_store = event_store
        self._project_dir = project_dir
        self._builder = builder
        self._tester = tester
        self._analyzer = analyzer
        # P0-2: Track which files each node touched, for cross-node dependency detection
        self._node_file_map: dict[str, list[str]] = {}
        self._dynamic_edges_injected: list[dict] = []

    async def execute(self, dag: TaskDAG, base_spec: Specification) -> DAGResult:
        max_reruns = 3  # Safety limit: prevent infinite re-run cycles
        rerun_cycles = 0

        while not dag.all_done():
            ready = dag.get_ready_nodes()
            if not ready:
                pending = [n for n in dag.nodes.values()
                           if n.status in (NodeStatus.PENDING, NodeStatus.NEEDS_RERUN)]
                if pending:
                    break  # Deadlock — pending nodes can never become ready
                continue

            for node in ready:
                dag.nodes[node.id].status = NodeStatus.RUNNING

            tasks = []
            for node in ready:
                tasks.append(self._run_node(dag, node, base_spec))
            await asyncio.gather(*tasks)

            # P0-2: Check for dynamic dependency injection after each batch
            if rerun_cycles < max_reruns:
                injected = self._check_and_inject_dynamic_edges(dag)
                if injected:
                    rerun_cycles += 1
                    # Re-queue affected nodes
                    for edge in injected:
                        dag.requeue_node(edge["affected_node"])
                    self._log(f"P0-2: Injected {len(injected)} dynamic edges, "
                              f"re-queued {len(injected)} nodes (cycle {rerun_cycles})")

        return dag.result()

    async def _run_node(self, dag: TaskDAG, node: TaskNode, base_spec: Specification) -> None:
        node_spec = self._build_node_spec(base_spec, node)
        controller = ASRController(
            config=self._config,
            event_store=self._event_store,
            project_dir=self._project_dir,
            builder=self._builder,
            tester=self._tester,
            analyzer=self._analyzer,
        )
        task_id = f"{dag.task_id}-{node.id}"
        result = await controller.run(node_spec, task_id=task_id)

        node.iterations = result.iterations
        if result.state == ConvergenceState.CONVERGED:
            dag.mark_completed(node.id, NodeStatus.CONVERGED)
        else:
            dag.mark_completed(node.id, NodeStatus.STUCK)

        # P0-2: Track which files this node touched (for dynamic edge detection)
        changed_files = self._extract_changed_files(result)
        if changed_files:
            self._node_file_map[node.id] = changed_files

    def _extract_changed_files(self, result) -> list[str]:
        """Extract files changed during this node's convergence run."""
        files: set[str] = set()
        for evt in result.events:
            if hasattr(evt, 'payload') and isinstance(evt.payload, dict):
                synced = evt.payload.get("synced_files", [])
                if synced:
                    files.update(synced)
        return list(files)

    def _check_and_inject_dynamic_edges(self, dag: TaskDAG) -> list[dict]:
        """P0-2: Detect cross-node file dependencies and inject dynamic edges.

        When node A modifies files that belong to node B's file list,
        inject a dependency: B should re-run because A changed its inputs.

        Returns list of injected edges: [{"source_node", "affected_node", "reason"}]
        """
        injected: list[dict] = []
        for source_id, changed_files in self._node_file_map.items():
            source_node = dag.nodes.get(source_id)
            if not source_node:
                continue
            for target_id, target_node in dag.nodes.items():
                if target_id == source_id:
                    continue
                if target_node.status not in (NodeStatus.CONVERGED, NodeStatus.STUCK):
                    continue  # Only re-queue completed nodes
                # Check if any changed file belongs to the target node's file list
                overlap = set(changed_files) & set(target_node.files)
                if overlap:
                    # Check if this edge already exists
                    if source_id in target_node.depends_on:
                        continue
                    # Check if reverse dependency exists (A depends on B → don't create cycle)
                    if target_id in source_node.depends_on:
                        continue
                    # Inject the edge: target now depends on source
                    reason = f"Node {source_id} modified files: {', '.join(sorted(overlap)[:3])}"
                    success = dag.inject_dependency(target_id, source_id, reason)
                    if success:
                        injected.append({
                            "source_node": source_id,
                            "affected_node": target_id,
                            "reason": reason,
                            "overlap_files": list(overlap),
                        })
                        self._dynamic_edges_injected.extend(injected)
                        self._log(f"P0-2: Injected edge {target_id} → {source_id} "
                                  f"(files: {', '.join(sorted(overlap)[:3])})")
        return injected

    def _log(self, msg: str) -> None:
        import sys
        print(f"[dag_executor] {msg}", file=sys.stderr)

    def _build_node_spec(self, base_spec: Specification, node: TaskNode) -> Specification:
        return Specification(
            goal=f"{base_spec.goal} — {node.name}",
            constraints=base_spec.constraints,
            acceptance=[
                a for a in base_spec.acceptance
                if any(f.replace(".py", "") in a.name.lower() or
                       f.replace("/", ".").replace(".py", "") in a.expected_behavior.lower()
                       for f in node.files)
            ] or base_spec.acceptance,
            features=[
                f for f in base_spec.features
                if f.name.lower().replace("_", "") in node.name.lower().replace("_", "")
            ] or base_spec.features,
        )
