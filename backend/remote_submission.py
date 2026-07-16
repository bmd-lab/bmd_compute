from __future__ import annotations

import re
from typing import Callable

from backend.remote import JobRecord, RemoteExecutionError, RemoteRunner
from backend.remote_runtime import connected_remote_runner, connection_profile_from_submission_spec


def submit_remote_workflow(
    submission_spec: dict,
    *,
    remote_prepared: bool,
    runner_factory: Callable[[], RemoteRunner] | None = None,
) -> dict:
    """
    Submit an already prepared workflow through the existing RemoteRunner path.

    This performs no monitoring or result parsing. The caller must indicate
    that the progressive Remote Preparation stage completed successfully.
    """

    if not remote_prepared:
        return _blocked_result()

    profile = connection_profile_from_submission_spec(submission_spec)

    try:
        with connected_remote_runner(
            profile=profile,
            runner_factory=runner_factory,
        ) as runner:
            record = runner.submit(submission_spec, dry_run=False)
    except Exception as exc:
        return _failure_result(exc)

    return _success_result(record)


def _success_result(record: JobRecord) -> dict:
    return {
        "status": "success",
        "title": "Submitted",
        "stage": "Submission",
        "job_id": record.job_id,
        "queue_status": "Submitted",
        "job_record": record.to_dict(),
        "ready_for_monitoring": False,
    }


def remembered_successful_submission(
    submission_spec: dict,
    job_id: str,
    *,
    submitted_at: str = "",
) -> dict:
    paths = submission_spec["paths"]
    record = JobRecord(
        job_id=job_id,
        run_name=submission_spec["run_name"],
        run_dir=paths["run_dir"],
        remote_script=paths["remote_script"],
        log_paths={
            "stdout": paths["log_out"],
            "stderr": paths["log_err"],
            "slurm_out": paths["slurm_out"],
            "slurm_err": paths["slurm_err"],
        },
        cluster=dict(submission_spec["cluster"]),
        resources=dict(submission_spec["resources"]),
        submitted_at=submitted_at,
        raw_output="",
        status="submitted",
        submission_spec=dict(submission_spec),
        remote_state_path=(
            f"{paths['logs_dir'].rstrip('/')}/job_{job_id}.json"
            if job_id
            else None
        ),
    )
    return _success_result(record)


def _blocked_result() -> dict:
    return {
        "status": "failed",
        "title": "Submission Blocked",
        "stage": "Submission",
        "reason": "Remote preparation has not completed successfully.",
        "suggestion": "Run Prepare Remote successfully before submitting the workflow.",
        "queue_status": "Not submitted",
    }


def _failure_result(exc: Exception) -> dict:
    details = _submission_failure_details(exc)
    result = {
        "status": "failed",
        "title": "Submission Failed",
        "stage": "Submission",
        "reason": details["reason"],
        "suggestion": _submission_suggestion(exc, details["reason"]),
        "queue_status": "Not submitted",
    }

    for key in ("exit_code", "stdout", "stderr", "streams_empty"):
        if key in details:
            result[key] = details[key]

    return result


def _submission_failure_details(exc: Exception) -> dict:
    if isinstance(exc, RemoteExecutionError):
        stdout, stderr = _remote_streams(exc)
        exit_code = exc.result.returncode
        return {
            "reason": _reason_from_remote_streams(stdout, stderr, exit_code),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "streams_empty": not stdout and not stderr,
        }

    return {
        "reason": _submission_reason(exc),
    }


def _submission_reason(exc: Exception) -> str:
    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None) == "paramiko":
        return "Paramiko is not installed in this Python environment."

    class_name = exc.__class__.__name__
    if "Authentication" in class_name or class_name in {"BadAuthenticationType", "PasswordRequiredException"}:
        return "SSH authentication failed."

    return _clean_message(str(exc) or class_name)


def _submission_suggestion(exc: Exception, reason: str | None = None) -> str:
    message = (reason or _submission_reason(exc)).lower()
    class_name = exc.__class__.__name__

    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None) == "paramiko":
        return "Install the project environment dependencies and try again."

    if "Authentication" in class_name or class_name in {"BadAuthenticationType", "PasswordRequiredException"}:
        return "Check SSH key or agent access and try again."

    if _looks_like_connection_failure(message):
        return "Connect to the TAU VPN and try again."

    if "permission denied" in message:
        return "Check permissions for the configured remote flows, logs, and POTCAR paths."

    if "invalid account" in message or "invalid partition" in message or "sbatch" in message:
        return "Check the configured SLURM partition, account, resources, and sbatch output."

    if "could not parse job id" in message:
        return "Check the raw sbatch output and SLURM submission configuration."

    return "Check the remote submission output and cluster environment."


def _remote_streams(exc: RemoteExecutionError) -> tuple[str, str]:
    result = exc.result
    return (
        _clean_stream(_submission_stream_output(result.stdout or "")),
        _clean_stream(_submission_stream_output(result.stderr or "")),
    )


def _submission_stream_output(output: str) -> str:
    lines = []
    for line in str(output or "").splitlines():
        if line.startswith("Submitting with:"):
            continue
        if line.startswith("SBATCH_RAW_OUT="):
            value = line.split("=", 1)[1]
            if value.strip():
                lines.append(value)
            continue
        lines.append(line)
    return "\n".join(lines)


def _reason_from_remote_streams(stdout: str, stderr: str, exit_code: int) -> str:
    if not stdout and not stderr:
        return f"Remote submission exited with code {exit_code}. No stdout or stderr was returned by sbatch."

    parts = [f"Remote submission exited with code {exit_code}."]
    if stdout:
        parts.append(f"stdout: {_one_line(stdout)}")
    if stderr:
        parts.append(f"stderr: {_one_line(stderr)}")
    return _clean_message(" ".join(parts))


def _clean_message(message: str) -> str:
    cleaned = _clean_stream(message)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:360]


def _clean_stream(message: str) -> str:
    lines = [
        line.strip()
        for line in str(message or "").splitlines()
        if line.strip()
    ]
    filtered = []
    in_traceback = False

    for line in lines:
        if line.startswith("Traceback "):
            in_traceback = True
            continue

        if in_traceback:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*: ", line):
                filtered.append(line)
                in_traceback = False
            continue

        if line.startswith('File "') or line.startswith("^"):
            continue
        filtered.append(line)

    cleaned = "\n".join(filtered).strip()
    return cleaned[:2000]


def _one_line(message: str) -> str:
    return re.sub(r"\s+", " ", str(message or "")).strip()[:240]


def _looks_like_connection_failure(message: str) -> bool:
    return any(
        fragment in message
        for fragment in (
            "ssh transport",
            "remote client is not connected",
            "timed out",
            "connection refused",
            "connection reset",
            "name or service not known",
            "nodename nor servname",
            "network is unreachable",
            "no route to host",
            "unable to connect",
        )
    )


__all__ = [
    "remembered_successful_submission",
    "submit_remote_workflow",
]
