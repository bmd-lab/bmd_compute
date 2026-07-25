from __future__ import annotations

import logging
import os
import shutil
import sys
from contextlib import contextmanager


def print_runtime_info() -> None:
    print("[runner] python:", sys.version.replace("\n", " "))

    for package in ("atomate2", "jobflow", "pymatgen", "custodian"):
        try:
            module = __import__(package)
            version = getattr(module, "__version__", "<no __version__>")
            print(f"[runner] {package} version:", version)
        except Exception as exc:
            print(f"[runner] {package} import failed:", exc)


def print_vasp_launch_environment() -> None:
    print(
        "[runner diagnostics] PATH:",
        os.environ.get("PATH", ""),
        file=sys.stderr,
        flush=True,
    )
    print(
        "[runner diagnostics] which vasp_std:",
        shutil.which("vasp_std") or "<not found>",
        file=sys.stderr,
        flush=True,
    )
    print(
        "[runner diagnostics] which mpirun:",
        shutil.which("mpirun") or "<not found>",
        file=sys.stderr,
        flush=True,
    )
    print(
        "[runner diagnostics] VASP_CMD:",
        os.environ.get("VASP_CMD", "<unset>"),
        file=sys.stderr,
        flush=True,
    )


def run_submission(spec: dict) -> None:
    print_runtime_info()

    from backend.parser import structure_from_spec
    from backend.workflows import build_atomate2_flow_from_spec
    from jobflow.managers.local import run_locally

    flow_spec = spec["flow_spec"]
    structure = structure_from_spec(flow_spec["structure"])
    flow = build_atomate2_flow_from_spec(
        structure,
        flow_spec,
        run_name=spec["run_name"],
    )

    print_vasp_launch_environment()

    with _jobflow_failure_tracebacks_to_stderr():
        try:
            run_locally(flow, ensure_success=True, create_folders=False)
        except TypeError:
            run_locally(flow, ensure_success=True)
    print("JOBFLOW_LOCAL_DONE")


@contextmanager
def _jobflow_failure_tracebacks_to_stderr():
    """
    Mirror Jobflow's swallowed job exception tracebacks to the sbatch .err log.

    run_locally(..., ensure_success=True) catches job-level exceptions, logs
    them, and later raises a wrapper RuntimeError if the flow did not finish.
    This handler preserves that behavior while ensuring the original traceback
    is visible in stderr before the wrapper RuntimeError is raised.
    """

    logger = logging.getLogger("jobflow.managers.local")
    previous_level = logger.level

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("[jobflow] %(message)s"))
    handler.addFilter(_JobflowFailureTracebackFilter())

    logger.addHandler(handler)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)

    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)


class _JobflowFailureTracebackFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "failed with exception:" in record.getMessage()


__all__ = [
    "print_runtime_info",
    "print_vasp_launch_environment",
    "run_submission",
]
