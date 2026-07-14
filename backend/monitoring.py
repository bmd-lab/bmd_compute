from __future__ import annotations

import re
import time
from typing import Callable

from backend.remote import RemoteExecutionError, RemoteJobStatus


class MonitoringStageError(RuntimeError):
    def __init__(
        self,
        stage: str,
        reason: str,
        *,
        command: str = "",
        stdout: str = "",
        stderr: str = "",
        exception: Exception | None = None,
    ):
        self.stage = stage
        self.reason = reason
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.exception = exception
        self.exception_text = str(exception) if exception else ""
        super().__init__(reason)


PENDING_STATES = {
    "PENDING",
    "CONFIGURING",
    "SUSPENDED",
    "STAGE_OUT",
    "RESV_DEL_HOLD",
}
RUNNING_STATES = {
    "RUNNING",
    "COMPLETING",
}
FAILURE_PREFIXES = (
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "PREEMPTED",
)


def strip_job_id(job_id: str | None) -> str:
    return re.sub(r"\.(batch|extern)$", "", str(job_id or "").strip())


def classify_slurm_state(state: str | None, exit_code: str | None = None) -> str:
    state_upper = (state or "").upper()
    exit_text = str(exit_code or "")

    if state_upper in PENDING_STATES:
        return "PENDING"
    if state_upper in RUNNING_STATES:
        return "RUNNING"
    if state_upper.startswith("COMPLETED") and (not exit_text or exit_text.startswith("0:0")):
        return "SUCCESS"
    if state_upper.startswith(FAILURE_PREFIXES):
        return "FAILURE"
    if exit_text and not exit_text.startswith("0:0"):
        return "FAILURE"
    if state_upper:
        return "PENDING"
    return "UNKNOWN"


def parse_scontrol_output(output: str) -> dict:
    def pick(pattern: str) -> str:
        match = re.search(pattern, output or "")
        return match.group(1) if match else ""

    return {
        "state": pick(r"JobState=([^ \n]+)"),
        "stdout": pick(r"StdOut=([^ \n]+)"),
        "workdir": pick(r"WorkDir=([^ \n]+)"),
        "jobname": pick(r"JobName=([^ \n]+)"),
    }


def parse_sacct_row(job_id: str, output: str) -> dict:
    stripped_job_id = strip_job_id(job_id)
    wanted = {
        stripped_job_id,
        f"{stripped_job_id}.batch",
        f"{stripped_job_id}.extern",
    }

    for line in (output or "").splitlines()[:6]:
        parts = line.split("|")
        if parts and parts[0] in wanted and len(parts) >= 6:
            return {
                "state": parts[1],
                "exit": parts[2],
                "jobname": parts[3],
                "stdout": parts[4],
                "workdir": parts[5],
            }

    return {}


def remote_job_status_from_slurm_outputs(
    job_id: str,
    *,
    squeue_output: str = "",
    scontrol_output: str = "",
    sacct_output: str = "",
    sacct_brief_output: str = "",
    squeue_stderr: str = "",
    scontrol_stderr: str = "",
    sacct_stderr: str = "",
    sacct_brief_stderr: str = "",
    command: str = "",
) -> RemoteJobStatus:
    stripped_job_id = strip_job_id(job_id)
    try:
        scontrol_info = parse_scontrol_output(scontrol_output)
        squeue_state = _first_line(squeue_output)
        sacct_info = parse_sacct_row(stripped_job_id, sacct_output)
    except Exception as exc:
        raise MonitoringStageError(
            "parsing",
            "Failed to parse SLURM monitoring output.",
            command=command,
            stdout=_combined_output(
                squeue=squeue_output,
                scontrol=scontrol_output,
                sacct=sacct_output,
                sacct_brief=sacct_brief_output,
            ),
            stderr=_combined_output(
                squeue=squeue_stderr,
                scontrol=scontrol_stderr,
                sacct=sacct_stderr,
                sacct_brief=sacct_brief_stderr,
            ),
            exception=exc,
        ) from exc

    state = squeue_state or scontrol_info.get("state") or sacct_info.get("state") or "UNKNOWN"
    exit_code = sacct_info.get("exit") or None
    stdout_path = sacct_info.get("stdout") or scontrol_info.get("stdout") or None
    workdir = sacct_info.get("workdir") or scontrol_info.get("workdir") or None
    job_name = sacct_info.get("jobname") or scontrol_info.get("jobname") or None
    try:
        summary = classify_slurm_state(state, exit_code)
    except Exception as exc:
        raise MonitoringStageError(
            "state classification",
            "Failed to classify SLURM job state.",
            command=command,
            stdout=_combined_output(
                squeue=squeue_output,
                scontrol=scontrol_output,
                sacct=sacct_output,
                sacct_brief=sacct_brief_output,
            ),
            stderr=_combined_output(
                squeue=squeue_stderr,
                scontrol=scontrol_stderr,
                sacct=sacct_stderr,
                sacct_brief=sacct_brief_stderr,
            ),
            exception=exc,
        ) from exc
    brief = sacct_brief_output.strip() or f"{stripped_job_id}|{state}"

    return RemoteJobStatus(
        job_id=stripped_job_id,
        state=state,
        exit_code=exit_code,
        stdout_path=stdout_path,
        workdir=workdir,
        job_name=job_name,
        raw={
            "summary": summary,
            "brief": brief,
            "squeue": (squeue_output or "").strip(),
            "scontrol": (scontrol_output or "").strip(),
            "sacct": (sacct_output or "").strip(),
            "squeue_stderr": (squeue_stderr or "").strip(),
            "scontrol_stderr": (scontrol_stderr or "").strip(),
            "sacct_stderr": (sacct_stderr or "").strip(),
            "sacct_brief_stderr": (sacct_brief_stderr or "").strip(),
            "updated_at": time.time(),
        },
    )


def monitor_remote_job(
    submission_spec: dict,
    job_id: str,
    *,
    runner_factory: Callable | None = None,
) -> dict:
    stripped_job_id = strip_job_id(job_id)
    if not stripped_job_id:
        return _failure_result(
            "Job ID",
            "No SLURM job ID is available for monitoring.",
            "Submit the workflow successfully before checking queue status.",
        )

    from backend.paramiko_remote import ParamikoRemoteRunner
    from backend.remote_preparation import connection_profile_from_submission_spec

    runner = (runner_factory or ParamikoRemoteRunner)()
    profile = connection_profile_from_submission_spec(submission_spec)

    try:
        runner.connect(profile)
    except Exception as exc:
        return _exception_result(exc, default_stage="SSH Connection")

    try:
        status = runner.query_job(stripped_job_id)
    except Exception as exc:
        return _exception_result(exc, default_stage="Monitoring")
    finally:
        try:
            runner.close()
        except Exception:
            pass

    return _success_result(status)


def _success_result(status: RemoteJobStatus) -> dict:
    summary = status.raw.get("summary") or classify_slurm_state(status.state, status.exit_code)
    return {
        "status": "success",
        "title": "Monitoring",
        "stage": "Monitoring",
        "job_id": status.job_id,
        "slurm_state": status.state,
        "summary": summary,
        "exit_code": status.exit_code,
        "stdout_path": status.stdout_path,
        "workdir": status.workdir,
        "job_name": status.job_name,
        "brief": status.raw.get("brief", ""),
        "updated_at": status.raw.get("updated_at"),
        "job_status": status,
    }


def _exception_result(exc: Exception, *, default_stage: str = "Monitoring") -> dict:
    if isinstance(exc, MonitoringStageError):
        return _failure_result(
            exc.stage,
            _clean_message(exc.reason),
            _suggestion_for_stage(exc.stage),
            command=exc.command,
            stdout=exc.stdout,
            stderr=exc.stderr,
            exception_text=exc.exception_text,
        )

    if isinstance(exc, RemoteExecutionError):
        output = "\n".join(
            item
            for item in (
                exc.result.stdout or "",
                exc.result.stderr or "",
                str(exc),
            )
            if item
        )
        return _failure_result(
            default_stage,
            _clean_message(output),
            "Check SLURM command availability and the remote cluster connection.",
            command=exc.result.command,
            stdout=exc.result.stdout,
            stderr=exc.result.stderr,
            exception_text=str(exc),
        )

    class_name = exc.__class__.__name__
    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None) == "paramiko":
        return _failure_result(
            "SSH Client Setup",
            "Paramiko is not installed in this Python environment.",
            "Install the project environment dependencies and try again.",
            exception_text=str(exc),
        )
    if "Authentication" in class_name or class_name in {"BadAuthenticationType", "PasswordRequiredException"}:
        return _failure_result(
            "SSH Authentication",
            "SSH authentication failed.",
            "Check SSH key or agent access and try again.",
            exception_text=str(exc),
        )
    if default_stage == "SSH Connection" and _looks_like_connection_failure(exc):
        return _failure_result(
            "SSH Connection",
            "Unable to connect to the remote cluster.",
            "Connect to the TAU VPN and try again.",
            exception_text=str(exc),
        )

    return _failure_result(
        default_stage,
        _clean_message(str(exc) or class_name),
        "Check the remote cluster environment and try again.",
        exception_text=str(exc) or class_name,
    )


def _failure_result(
    stage: str,
    reason: str,
    suggestion: str,
    *,
    command: str = "",
    stdout: str = "",
    stderr: str = "",
    exception_text: str = "",
) -> dict:
    return {
        "status": "failed",
        "title": "Monitoring Failed",
        "stage": stage,
        "reason": reason,
        "suggestion": suggestion,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "exception_text": exception_text,
    }


def _first_line(output: str) -> str:
    for line in (output or "").splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def _clean_message(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(message or "").strip())
    return (cleaned or "Monitoring failed.")[:360]


def _combined_output(**items: str) -> str:
    return "\n".join(
        f"{name}:\n{value}"
        for name, value in items.items()
        if value
    )


def _suggestion_for_stage(stage: str) -> str:
    stage_lower = str(stage or "").lower()
    if "ssh" in stage_lower:
        return "Check SSH key, agent, VPN, and cluster login access."
    if stage_lower in {"squeue", "scontrol", "sacct"}:
        return "Inspect the command output and verify SLURM command availability for the submitted job."
    if stage_lower == "parsing":
        return "Inspect the raw scheduler output; the monitor could not parse it."
    if stage_lower == "state classification":
        return "Inspect the raw scheduler state and update the classifier if SLURM returned a new state."
    return "Check the remote cluster environment and try again."


def _looks_like_connection_failure(exc: Exception) -> bool:
    class_name = exc.__class__.__name__
    if class_name in {"NoValidConnectionsError", "TimeoutError", "gaierror"}:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    message = _clean_message(exc).lower()
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
    "classify_slurm_state",
    "monitor_remote_job",
    "MonitoringStageError",
    "parse_sacct_row",
    "parse_scontrol_output",
    "remote_job_status_from_slurm_outputs",
    "strip_job_id",
]
