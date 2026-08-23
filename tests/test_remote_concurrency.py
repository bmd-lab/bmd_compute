from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from starlette.requests import Request

import backend.remote_runtime as remote_runtime
import main
from backend import config
from backend.config import DEFAULT_REMOTE_HOST, DEFAULT_USERNAME, NOTEBOOK_DEFAULTS
from backend.monitoring import monitor_job
from backend.remote import JobRecord, RemoteCommandResult, RemoteConnectionProfile, RemoteJobStatus
from backend.remote_preparation import prepare_remote_submission
from backend.remote_runtime import RemoteOperationLimiter, connected_remote_runner
from backend.remote_submission import submit_remote_workflow
from backend.results import load_results_for_completed_job
from backend.submission import create_submission_spec


def profile() -> RemoteConnectionProfile:
    return RemoteConnectionProfile(
        host=NOTEBOOK_DEFAULTS["remote_host"],
        username=NOTEBOOK_DEFAULTS["username"],
        port=NOTEBOOK_DEFAULTS["port"],
        ssh_config_host=NOTEBOOK_DEFAULTS["ssh_config_host"],
    )


def install_limiter(monkeypatch, *, limit: int = 1, timeout_s: float = 0.02):
    limiter = RemoteOperationLimiter(limit=limit, acquire_timeout_s=timeout_s)
    monkeypatch.setattr(remote_runtime, "_REMOTE_OPERATION_LIMITER", limiter)
    return limiter


def test_remote_concurrency_environment_override_validation(monkeypatch):
    monkeypatch.setenv(config.REMOTE_OPERATION_LIMIT_ENV, "3")
    assert config._positive_int_env(
        config.REMOTE_OPERATION_LIMIT_ENV,
        default=4,
    ) == 3

    monkeypatch.setenv(config.REMOTE_OPERATION_SLOT_TIMEOUT_ENV, "0.25")
    assert config._positive_float_env(
        config.REMOTE_OPERATION_SLOT_TIMEOUT_ENV,
        default=5.0,
    ) == 0.25

    for value in ("0", "-1", "many"):
        monkeypatch.setenv(config.REMOTE_OPERATION_LIMIT_ENV, value)
        with pytest.raises(RuntimeError):
            config._positive_int_env(config.REMOTE_OPERATION_LIMIT_ENV, default=4)

    for value in ("0", "-0.1", "inf", "forever"):
        monkeypatch.setenv(config.REMOTE_OPERATION_SLOT_TIMEOUT_ENV, value)
        with pytest.raises(RuntimeError):
            config._positive_float_env(
                config.REMOTE_OPERATION_SLOT_TIMEOUT_ENV,
                default=5.0,
            )


def submission_spec():
    return create_submission_spec(
        {
            "workflow": "static",
            "potcar_functional": "PBE_64",
            "kpoints": None,
            "incar": {},
            "structure": {
                "type": "pasted_text",
                "format": "poscar",
                "text": "placeholder",
            },
        },
        label="Si static",
        timestamp="20260818-120000",
        env={},
    )


def prep_record(spec, *, raw_output: str | None = None, status: str = "dry_run"):
    return JobRecord(
        job_id=None if status == "dry_run" else "123456",
        run_name=spec["run_name"],
        run_dir=spec["paths"]["run_dir"],
        remote_script=spec["paths"]["remote_script"],
        log_paths={
            "stdout": spec["paths"]["log_out"],
            "stderr": spec["paths"]["log_err"],
            "slurm_out": spec["paths"]["slurm_out"],
            "slurm_err": spec["paths"]["slurm_err"],
        },
        cluster=dict(spec["cluster"]),
        resources=dict(spec["resources"]),
        submitted_at="2026-08-18 12:00:00",
        raw_output=raw_output
        or (
            "PREP_OK=Remote directories prepared\n"
            "PREP_OK=Working directory created\n"
            "PREP_OK=submission.json uploaded\n"
            "PREP_OK=Execution module uploaded\n"
            "PREP_OK=run_job.py uploaded\n"
            "PREP_OK=Runtime import preflight\n"
            "PREP_OK=Submission script written\n"
            "PREP_OK=Ready for submission\n"
        ),
        status=status,
    )


class BlockingPrepareRunner:
    lock = Lock()
    release = Event()
    connected_event = Event()
    live_connections = 0
    max_live_connections = 0
    expected_connections = 0
    created = 0

    @classmethod
    def reset(cls, *, expected_connections: int):
        with cls.lock:
            cls.live_connections = 0
            cls.max_live_connections = 0
            cls.expected_connections = expected_connections
            cls.created = 0
        cls.release = Event()
        cls.connected_event = Event()

    def __init__(self):
        self.connected = False
        self.closed = False
        with BlockingPrepareRunner.lock:
            BlockingPrepareRunner.created += 1

    def connect(self, connection_profile):
        self.connected = True
        with BlockingPrepareRunner.lock:
            BlockingPrepareRunner.live_connections += 1
            BlockingPrepareRunner.max_live_connections = max(
                BlockingPrepareRunner.max_live_connections,
                BlockingPrepareRunner.live_connections,
            )
            if BlockingPrepareRunner.live_connections >= BlockingPrepareRunner.expected_connections:
                BlockingPrepareRunner.connected_event.set()

    def submit(self, spec, dry_run=False):
        assert dry_run is True
        assert BlockingPrepareRunner.release.wait(timeout=5)
        return prep_record(spec)

    def close(self):
        with BlockingPrepareRunner.lock:
            if self.connected and not self.closed:
                BlockingPrepareRunner.live_connections -= 1
            self.closed = True


def test_remote_operation_limit_rejects_excess_without_extra_connection(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=2, timeout_s=0.02)
    spec = submission_spec()
    BlockingPrepareRunner.reset(expected_connections=2)

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(
            prepare_remote_submission,
            spec,
            runner_factory=BlockingPrepareRunner,
        )
        second = executor.submit(
            prepare_remote_submission,
            spec,
            runner_factory=BlockingPrepareRunner,
        )
        assert BlockingPrepareRunner.connected_event.wait(timeout=5)

        started = time.monotonic()
        busy = prepare_remote_submission(
            spec,
            runner_factory=BlockingPrepareRunner,
        )
        elapsed = time.monotonic() - started

        BlockingPrepareRunner.release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert all(result["status"] == "success" for result in results)
    assert busy["status"] == "failed"
    assert busy["stage"] == "Remote Capacity"
    assert "handling several remote operations" in busy["reason"]
    assert elapsed < 1
    assert BlockingPrepareRunner.created == 2
    assert BlockingPrepareRunner.max_live_connections == 2
    assert limiter.snapshot()["max_active_observed"] == 2
    assert limiter.snapshot()["busy_rejections"] == 1
    assert limiter.snapshot()["active"] == 0


def test_slot_release_on_success_allows_waiting_operation(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=1, timeout_s=1.0)
    spec = submission_spec()
    BlockingPrepareRunner.reset(expected_connections=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            prepare_remote_submission,
            spec,
            runner_factory=BlockingPrepareRunner,
        )
        assert BlockingPrepareRunner.connected_event.wait(timeout=5)
        second = executor.submit(
            prepare_remote_submission,
            spec,
            runner_factory=lambda: ImmediatePrepareRunner(),
        )
        time.sleep(0.02)
        BlockingPrepareRunner.release.set()

        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert first_result["status"] == "success"
    assert second_result["status"] == "success"
    assert limiter.snapshot()["active"] == 0
    assert limiter.snapshot()["max_active_observed"] == 1


class ImmediatePrepareRunner:
    connected_count = 0
    closed_count = 0

    def connect(self, connection_profile):
        ImmediatePrepareRunner.connected_count += 1

    def submit(self, spec, dry_run=False):
        return prep_record(spec, status="dry_run" if dry_run else "submitted")

    def close(self):
        ImmediatePrepareRunner.closed_count += 1


class ConnectFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, connection_profile):
        raise OSError("network is unreachable")

    def close(self):
        self.closed = True


class SubmitFailureRunner(ImmediatePrepareRunner):
    def submit(self, spec, dry_run=False):
        raise RuntimeError("remote command failed")


class TimeoutRunner(ImmediatePrepareRunner):
    def run(self, command):
        raise TimeoutError("remote command timed out")


class SftpFailureRunner(ImmediatePrepareRunner):
    def put_text(self, remote_path, text, *, mode=0o640):
        raise OSError("simulated SFTP write failure")


def test_slots_release_after_connect_and_command_failures(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=1, timeout_s=0.02)
    spec = submission_spec()

    connect_failure = prepare_remote_submission(
        spec,
        runner_factory=ConnectFailureRunner,
    )
    command_failure = prepare_remote_submission(
        spec,
        runner_factory=SubmitFailureRunner,
    )

    assert connect_failure["status"] == "failed"
    assert command_failure["status"] == "failed"
    assert limiter.snapshot()["active"] == 0

    success = prepare_remote_submission(
        spec,
        runner_factory=ImmediatePrepareRunner,
    )
    assert success["status"] == "success"
    assert limiter.snapshot()["active"] == 0


def test_slots_release_after_sftp_exception_and_timeout(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=1, timeout_s=0.02)

    with pytest.raises(OSError):
        with connected_remote_runner(
            profile=profile(),
            runner_factory=SftpFailureRunner,
        ) as runner:
            runner.put_text("/remote/file.txt", "payload")

    with pytest.raises(TimeoutError):
        with connected_remote_runner(
            profile=profile(),
            runner_factory=TimeoutRunner,
        ) as runner:
            runner.run("sleep 999")

    assert limiter.snapshot()["active"] == 0


class MonitorRunner:
    connected = 0
    closed = 0

    def connect(self, connection_profile):
        MonitorRunner.connected += 1

    def query_job(self, job_id):
        return RemoteJobStatus(
            job_id=job_id,
            state="RUNNING",
            raw={"summary": "RUNNING", "brief": f"{job_id}|RUNNING"},
        )

    def close(self):
        MonitorRunner.closed += 1


def test_monitoring_and_resume_each_use_one_remote_slot(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=1, timeout_s=0.02)
    spec = submission_spec()
    MonitorRunner.connected = 0
    MonitorRunner.closed = 0

    submitted = monitor_job(
        "123456",
        submission_spec=spec,
        runner_factory=MonitorRunner,
    )
    resumed = monitor_job(
        "123456",
        runner_factory=MonitorRunner,
    )

    assert submitted["status"] == "success"
    assert resumed["status"] == "success"
    assert MonitorRunner.connected == 2
    assert MonitorRunner.closed == 2
    assert limiter.snapshot()["max_active_observed"] == 1
    assert limiter.snapshot()["active"] == 0


class ResultsFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, connection_profile):
        self.profile = connection_profile

    def close(self):
        self.closed = True

    def is_file(self, remote_path):
        return True

    def run_python(self, source, *, python, env=None, check=False, timeout_s=None):
        return RemoteCommandResult(
            command="remote parser",
            returncode=2,
            stderr="remote parser failed",
        )


def completed_monitoring():
    return {
        "status": "success",
        "job_id": "123456",
        "slurm_state": "COMPLETED",
        "summary": "SUCCESS",
        "exit_code": "0:0",
    }


def test_load_results_uses_one_slot_and_releases_after_parser_failure(monkeypatch):
    limiter = install_limiter(monkeypatch, limit=1, timeout_s=0.02)
    spec = submission_spec()
    runner = ResultsFailureRunner()

    result = load_results_for_completed_job(
        completed_monitoring(),
        submission_spec=spec,
        runner_factory=lambda: runner,
        cache=False,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "Results Parsing"
    assert runner.closed is True
    assert limiter.snapshot()["max_active_observed"] == 1
    assert limiter.snapshot()["active"] == 0


def test_busy_responses_for_submit_monitor_and_results(monkeypatch):
    install_limiter(monkeypatch, limit=1, timeout_s=0.01)
    spec = submission_spec()
    BlockingPrepareRunner.reset(expected_connections=1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(
            prepare_remote_submission,
            spec,
            runner_factory=BlockingPrepareRunner,
        )
        assert BlockingPrepareRunner.connected_event.wait(timeout=5)

        submit_result = submit_remote_workflow(
            spec,
            remote_prepared=True,
            runner_factory=ImmediatePrepareRunner,
        )
        monitor_result = monitor_job(
            "123456",
            submission_spec=spec,
            runner_factory=MonitorRunner,
        )
        results_result = load_results_for_completed_job(
            completed_monitoring(),
            submission_spec=spec,
            runner_factory=ResultsFailureRunner,
            cache=False,
        )

        BlockingPrepareRunner.release.set()
        holder.result(timeout=5)

    assert submit_result["status"] == "failed"
    assert "handling several remote operations" in submit_result["reason"]
    assert monitor_result["status"] == "failed"
    assert monitor_result["stage"] == "Remote Capacity"
    assert "exception_debug" not in monitor_result
    assert results_result["status"] == "failed"
    assert results_result["stage"] == "Remote Capacity"
    assert "traceback" not in results_result


def test_local_home_route_stays_responsive_while_remote_slots_are_saturated(monkeypatch):
    install_limiter(monkeypatch, limit=1, timeout_s=0.01)

    with connected_remote_runner(
        profile=profile(),
        runner_factory=ImmediatePrepareRunner,
    ):
        response = main.home(
            Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "headers": [],
                }
            )
        )

    assert response.status_code == 200
    assert response.context["results_summary"] is None
