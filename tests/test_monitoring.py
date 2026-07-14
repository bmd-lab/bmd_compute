from backend.monitoring import (
    MonitoringStageError,
    classify_slurm_state,
    monitor_remote_job,
    parse_sacct_row,
    parse_scontrol_output,
    remote_job_status_from_slurm_outputs,
    strip_job_id,
)
from backend.remote import RemoteJobStatus


def test_strip_job_id_removes_slurm_suffixes():
    assert strip_job_id("12345.batch") == "12345"
    assert strip_job_id("12345.extern") == "12345"
    assert strip_job_id(" 12345 ") == "12345"


def test_classify_slurm_state_matches_notebook_rules():
    assert classify_slurm_state("PENDING") == "PENDING"
    assert classify_slurm_state("CONFIGURING") == "PENDING"
    assert classify_slurm_state("RUNNING") == "RUNNING"
    assert classify_slurm_state("COMPLETING") == "RUNNING"
    assert classify_slurm_state("COMPLETED", "0:0") == "SUCCESS"
    assert classify_slurm_state("FAILED", "1:0") == "FAILURE"
    assert classify_slurm_state("COMPLETED", "2:0") == "FAILURE"
    assert classify_slurm_state("") == "UNKNOWN"


def test_parse_scontrol_output_extracts_monitoring_fields():
    output = (
        "JobId=12345 JobName=TiO2-static\n"
        "   JobState=RUNNING Reason=None StdOut=/logs/job.out WorkDir=/flows/run\n"
    )

    parsed = parse_scontrol_output(output)

    assert parsed["state"] == "RUNNING"
    assert parsed["stdout"] == "/logs/job.out"
    assert parsed["workdir"] == "/flows/run"
    assert parsed["jobname"] == "TiO2-static"


def test_parse_sacct_row_prefers_matching_job_id():
    output = (
        "999|FAILED|1:0|other|/logs/other.out|/flows/other\n"
        "12345|COMPLETED|0:0|TiO2-static|/logs/job.out|/flows/run\n"
    )

    parsed = parse_sacct_row("12345.batch", output)

    assert parsed["state"] == "COMPLETED"
    assert parsed["exit"] == "0:0"
    assert parsed["jobname"] == "TiO2-static"
    assert parsed["stdout"] == "/logs/job.out"
    assert parsed["workdir"] == "/flows/run"


def test_remote_job_status_merges_squeue_scontrol_and_sacct():
    status = remote_job_status_from_slurm_outputs(
        "12345",
        squeue_output="",
        scontrol_output="JobId=12345 JobName=TiO2-static JobState=COMPLETED StdOut=/logs/current.out WorkDir=/flows/current",
        sacct_output="12345|COMPLETED|0:0|TiO2-static|/logs/final.out|/flows/final",
        sacct_brief_output="12345|TiO2-static|COMPLETED|00:02:10|2026|2026|leeburton-pool",
    )

    assert isinstance(status, RemoteJobStatus)
    assert status.job_id == "12345"
    assert status.state == "COMPLETED"
    assert status.exit_code == "0:0"
    assert status.stdout_path == "/logs/final.out"
    assert status.workdir == "/flows/final"
    assert status.job_name == "TiO2-static"
    assert status.raw["summary"] == "SUCCESS"
    assert "TiO2-static" in status.raw["brief"]


def test_remote_job_status_reports_parsing_stage_with_raw_output():
    try:
        remote_job_status_from_slurm_outputs(
            "12345",
            squeue_output="RUNNING\n",
            scontrol_output=object(),
            command="/usr/bin/scontrol show job 12345",
        )
    except MonitoringStageError as exc:
        assert exc.stage == "parsing"
        assert "scontrol" in exc.command
        assert "RUNNING" in exc.stdout
        assert exc.exception_text
    else:
        raise AssertionError("MonitoringStageError was not raised")


class FakeRunner:
    def __init__(self, status):
        self.status = status
        self.connected = False
        self.closed = False

    def connect(self, profile):
        self.connected = True
        self.profile = profile

    def query_job(self, job_id):
        assert job_id == "12345"
        return self.status

    def close(self):
        self.closed = True


def test_monitor_remote_job_uses_runner_and_closes_connection():
    status = RemoteJobStatus(
        job_id="12345",
        state="RUNNING",
        stdout_path="/logs/job.out",
        workdir="/flows/run",
        job_name="TiO2-static",
        raw={"summary": "RUNNING", "brief": "12345|TiO2-static|RUNNING"},
    )
    runner = FakeRunner(status)
    submission_spec = {
        "cluster": {
            "remote_host": "powerslurm-login.tau.ac.il",
            "username": "leeburton",
        }
    }

    result = monitor_remote_job(
        submission_spec,
        "12345.batch",
        runner_factory=lambda: runner,
    )

    assert runner.connected is True
    assert runner.closed is True
    assert result["status"] == "success"
    assert result["job_id"] == "12345"
    assert result["slurm_state"] == "RUNNING"
    assert result["summary"] == "RUNNING"
    assert result["brief"] == "12345|TiO2-static|RUNNING"


def test_monitor_remote_job_rejects_missing_job_id():
    result = monitor_remote_job({}, "")

    assert result["status"] == "failed"
    assert result["stage"] == "Job ID"


class FailingQueryRunner(FakeRunner):
    def __init__(self):
        super().__init__(None)

    def query_job(self, job_id):
        raise MonitoringStageError(
            "squeue",
            "squeue command failed before returning a result.",
            command="/usr/bin/squeue -j 12345 -h -o %T",
            stdout="partial stdout",
            stderr="squeue stderr",
            exception=TimeoutError("squeue timed out"),
        )


def test_monitor_remote_job_preserves_query_failure_stage_and_streams():
    runner = FailingQueryRunner()
    submission_spec = {
        "cluster": {
            "remote_host": "powerslurm-login.tau.ac.il",
            "username": "leeburton",
        }
    }

    result = monitor_remote_job(
        submission_spec,
        "12345",
        runner_factory=lambda: runner,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "squeue"
    assert "/usr/bin/squeue" in result["command"]
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "squeue stderr"
    assert result["exception_text"] == "squeue timed out"


class FailingConnectRunner(FakeRunner):
    def __init__(self):
        super().__init__(None)

    def connect(self, profile):
        raise OSError("network is unreachable")


def test_monitor_remote_job_reports_connection_failures_only_during_connect():
    result = monitor_remote_job(
        {
            "cluster": {
                "remote_host": "powerslurm-login.tau.ac.il",
                "username": "leeburton",
            }
        },
        "12345",
        runner_factory=FailingConnectRunner,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "SSH Connection"
    assert "network is unreachable" in result["exception_text"]


if __name__ == "__main__":
    test_strip_job_id_removes_slurm_suffixes()
    test_classify_slurm_state_matches_notebook_rules()
    test_parse_scontrol_output_extracts_monitoring_fields()
    test_parse_sacct_row_prefers_matching_job_id()
    test_remote_job_status_merges_squeue_scontrol_and_sacct()
    test_remote_job_status_reports_parsing_stage_with_raw_output()
    test_monitor_remote_job_uses_runner_and_closes_connection()
    test_monitor_remote_job_rejects_missing_job_id()
    test_monitor_remote_job_preserves_query_failure_stage_and_streams()
    test_monitor_remote_job_reports_connection_failures_only_during_connect()
    print("monitoring smoke test passed")
