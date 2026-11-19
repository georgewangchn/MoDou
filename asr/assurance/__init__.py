"""MoDou assurance package.

- ``project``  — mode A: project-level independent assessment (``asr assess``)
- ``grading`` / ``dossier`` / ``assessor`` / ``compose`` / ``pipeline`` /
  ``store`` — mode C: board assurance over an OuSheng board (shadow, read-only)

Mode A's public API is re-exported here so existing callers
(``asr.cli``, tests) keep working unchanged.
"""
from asr.assurance.project import (  # noqa: F401
    STATUS_BLOCKED,
    STATUS_DEVIATION,
    STATUS_INCONCLUSIVE,
    STATUS_PASS,
    Assessment,
    Finding,
    Verification,
    _check_acceptance,
    _determine_status,
    assess,
    run_verification,
)
