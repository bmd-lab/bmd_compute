from __future__ import annotations

import logging
from contextlib import redirect_stderr
from io import StringIO

from backend.execution import _jobflow_failure_tracebacks_to_stderr


logger = logging.getLogger("jobflow.managers.local")
stderr = StringIO()

with redirect_stderr(stderr), _jobflow_failure_tracebacks_to_stderr():
    logger.info("Started executing jobs locally")
    logger.info(
        "Static failed with exception:\n"
        "Traceback (most recent call last):\n"
        "  File \"job.py\", line 1, in run\n"
        "RuntimeError: underlying atomate2 failure\n"
    )

output = stderr.getvalue()

assert "Started executing jobs locally" not in output
assert "Static failed with exception:" in output
assert "Traceback (most recent call last):" in output
assert "RuntimeError: underlying atomate2 failure" in output

print("execution diagnostics smoke test passed")
