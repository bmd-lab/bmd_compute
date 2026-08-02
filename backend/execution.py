from __future__ import annotations

import logging
import os
import shutil
import sys
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path


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
        resources=spec.get("resources"),
    )

    print_vasp_launch_environment()

    with _jobflow_failure_tracebacks_to_stderr():
        stage_directories = _flow_stage_directories(flow)
        if stage_directories:
            _run_locally_with_stage_directories(
                flow,
                stage_directories,
                ensure_success=True,
            )
        else:
            try:
                run_locally(flow, ensure_success=True, create_folders=False)
            except TypeError:
                run_locally(flow, ensure_success=True)
    print("JOBFLOW_LOCAL_DONE")


def _flow_stage_directories(flow) -> tuple[str, ...]:
    jobs = list(getattr(flow, "jobs", []) or [])
    if len(jobs) <= 1:
        return ()

    metadata = getattr(flow, "metadata", None) or {}
    configured = metadata.get("bmd_stage_directories") or getattr(
        flow,
        "bmd_stage_directories",
        (),
    )
    if configured:
        return tuple(_safe_stage_directory_name(name, index) for index, name in enumerate(configured, 1))

    return tuple(
        _safe_stage_directory_name(getattr(job, "name", ""), index)
        for index, job in enumerate(jobs, 1)
    )


def _safe_stage_directory_name(name, index: int) -> str:
    raw = str(name or "").strip().replace("\\", "/").split("/")[-1]
    safe = "".join(
        character if character.isalnum() or character in {"_", "-"} else "_"
        for character in raw
    ).strip("_-")
    return safe or f"stage_{index:02d}"


def _run_locally_with_stage_directories(
    flow,
    stage_directories: tuple[str, ...],
    *,
    ensure_success: bool = False,
    root_dir: str | Path | None = None,
    log: bool | str = True,
    store=None,
    allow_external_references: bool = False,
    raise_immediately: bool = False,
):
    from jobflow import SETTINGS, initialize_logger
    from jobflow.core.flow import get_flow
    from jobflow.core.reference import OnMissing

    if store is None:
        store = SETTINGS.JOB_STORE

    root = Path.cwd() if root_dir is None else Path(root_dir).resolve()
    root.mkdir(exist_ok=True)
    store.connect()

    if log:
        initialize_logger(fmt=log if isinstance(log, str) else "")

    root_flow = get_flow(flow, allow_external_references=allow_external_references)
    logger = logging.getLogger("jobflow.managers.local")
    stopped_parents: set[str] = set()
    errored: set[str] = set()
    responses = defaultdict(dict)
    stop_jobflow = False

    def _run_job(job, parents):
        nonlocal stop_jobflow

        if stop_jobflow:
            return None, True
        if set(parents).intersection(stopped_parents):
            logger.info(
                f"{job.name} is a child of a job with stop_children=True, skipping..."
            )
            stopped_parents.add(job.uuid)
            return None, False
        if (
            set(parents).intersection(errored)
            and job.config.on_missing_references == OnMissing.ERROR
        ):
            errored.add(job.uuid)
            return None, False

        if raise_immediately:
            response = job.run(store=store)
        else:
            try:
                response = job.run(store=store)
            except Exception:
                import traceback

                logger.info(
                    f"{job.name} failed with exception:\n{traceback.format_exc()}"
                )
                errored.add(job.uuid)
                return None, False

        responses[job.uuid][job.index] = response

        if response.stored_data is not None:
            logger.warning("Response.stored_data is not supported with local manager.")
        if response.stop_children:
            stopped_parents.add(job.uuid)
        if response.stop_jobflow:
            stop_jobflow = True
            return None, True

        diversion_responses = []
        if response.replace is not None:
            diversion_responses.append(_run(response.replace))
        if response.detour is not None:
            diversion_responses.append(_run(response.detour))
        if response.addition is not None:
            diversion_responses.append(_run(response.addition))
        if not all(diversion_responses):
            return None, False

        return response, False

    def _stage_directory(index: int) -> Path:
        if index < len(stage_directories):
            return root / stage_directories[index]
        return root / f"stage_{index + 1:02d}"

    def _run(current_flow):
        encountered_bad_response = False
        for index, (job, parents) in enumerate(current_flow.iterflow()):
            job_dir = _stage_directory(index)
            job_dir.mkdir(exist_ok=True)
            with _change_directory(job_dir):
                response, jobflow_stopped = _run_job(job, parents)
            if response is not None:
                response.job_dir = job_dir
            encountered_bad_response = encountered_bad_response or response is None
            if jobflow_stopped:
                return False

        return not encountered_bad_response

    logger.info("Started executing jobs locally")
    finished_successfully = _run(root_flow)
    logger.info("Finished executing jobs locally")
    if ensure_success and not finished_successfully:
        raise RuntimeError("Flow did not finish running successfully")

    return dict(responses)


@contextmanager
def _change_directory(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


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
    "_flow_stage_directories",
    "_run_locally_with_stage_directories",
    "print_runtime_info",
    "print_vasp_launch_environment",
    "run_submission",
]
