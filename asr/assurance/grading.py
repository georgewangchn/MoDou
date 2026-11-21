"""Tiering router — which board items enter MODOU analysis at all.

Final-1.0 §4: value-driven sampling, not uniform coverage.
Keys are cheap objective field reads; ambiguous attribution (item↔design
weak links) is deferred to the LLM assessor, not encoded here (绳不做语义判断).

T0 must-enter : design closure (decided designs' related_items),
                 breaking contracts, release/deployment-bearing items
T1 sampled    : remaining done/testing task|bug items
T2 skip       : notice, cancelled, backlog/ready (nothing assessable),
                 items outside all of the above with no deliverable state
"""
from __future__ import annotations

from dataclasses import dataclass, field

from asr.adapters.ousheng.snapshot import BoardSnapshot

T0, T1, T2 = "T0", "T1", "T2"

# Types whose deliverable is a release/deployment act — version axis applies
RELEASE_TYPES = {"release", "deployment"}

# Statuses that carry an assessable deliverable
DELIVERABLE_STATUSES = {"done", "testing"}


@dataclass
class GradeResult:
    tiers: dict[str, str] = field(default_factory=dict)     # item_id → T0|T1|T2
    reasons: dict[str, str] = field(default_factory=dict)   # item_id → reason
    sample_pick: set[str] = field(default_factory=set)      # T1 items actually sampled

    def counts(self) -> dict[str, int]:
        c = {T0: 0, T1: 0, T2: 0}
        for t in self.tiers.values():
            c[t] += 1
        return c

    def of(self, tier: str) -> list[str]:
        return [i for i, t in self.tiers.items() if t == tier]


def grade_board(snapshot: BoardSnapshot, sample_rate: float = 0.0,
                sampler=None) -> GradeResult:
    """Grade every work item. ``sampler`` (callable n→bool-list) is
    injectable for deterministic tests; default uses ``random``."""
    res = GradeResult()
    closure = snapshot.t0_closure()

    for iid, item in snapshot.work_items.items():
        if item.type == "notice":
            res.tiers[iid], res.reasons[iid] = T2, "notice: broadcast-only"
        elif item.status == "cancelled":
            res.tiers[iid], res.reasons[iid] = T2, "cancelled"
        elif item.status not in DELIVERABLE_STATUSES:
            res.tiers[iid], res.reasons[iid] = T2, f"status={item.status or '?'}: no assessable deliverable"
        elif iid in closure:
            res.tiers[iid], res.reasons[iid] = T0, "in decided-design related_items closure"
        elif item.contract.get("breaking") is True:
            res.tiers[iid], res.reasons[iid] = T0, "contract.breaking=true"
        elif item.type in RELEASE_TYPES or item.target_version:
            res.tiers[iid], res.reasons[iid] = T0, "release/deployment axis (target_version)"
        else:
            res.tiers[iid], res.reasons[iid] = T1, f"ordinary {item.type} in {item.status}"

    if sample_rate > 0:
        t1 = res.of(T1)
        if sampler is not None:
            picks = [i for i, keep in zip(t1, sampler(len(t1)), strict=False) if keep]
        else:
            import random
            picks = random.sample(t1, k=round(len(t1) * sample_rate)) if t1 else []
        res.sample_pick = set(picks)
    return res
