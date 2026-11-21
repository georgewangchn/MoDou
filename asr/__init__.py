"""MoDou（墨斗）Assurance Runtime — public API surface (v1, Experimental).

Programmatic entry points for embedding runtimes (OuSheng cruise, MOSS
decision feedback, CI pipelines):

    import asyncio, asr
    assessment = asyncio.run(asr.assess(project_dir, spec_path, use_analyzer=False))
    if assessment.status == asr.STATUS_PASS: ...

Contract:
- ``API_VERSION`` identifies this surface; breaking changes bump it.
- Only names in ``__all__`` are public. Everything else is internal and may
  change without notice.
- ``assess`` is read-only towards the assessed project; ``run`` (convergence
  plugin) is NOT re-exported here — it mutates projects and stays CLI-only
  by design.
"""
from asr.assurance import (  # noqa: F401
    STATUS_BLOCKED,
    STATUS_DEVIATION,
    STATUS_INCONCLUSIVE,
    STATUS_PASS,
    Assessment,
    Finding,
    Verification,
    assess,
    run_verification,
)

__version__ = "3.1.0"

# Semantic identifier of this public surface (semver-tracked via __version__).
API_VERSION = "modou/public-api@1"

__all__ = [
    "API_VERSION",
    "STATUS_BLOCKED",
    "STATUS_DEVIATION",
    "STATUS_INCONCLUSIVE",
    "STATUS_PASS",
    "Assessment",
    "Finding",
    "Verification",
    "__version__",
    "assess",
    "run_verification",
]
