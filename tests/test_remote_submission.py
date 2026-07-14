from backend.config import DEFAULT_LOGS_DIR, DEFAULT_REMOTE_HOST
from backend.remote import JobRecord, RemoteCommandResult, RemoteExecutionError
from backend.remote_submission import submit_remote_workflow
from backend.submission import create_submission_spec


flow_spec = {
    "workflow": "static",
    "potcar_functional": "PBE_64",
    "kpoints": None,
    "incar": {},
    "structure": {
        "type": "pasted_text",
        "format": "poscar",
        "text": "placeholder",
    },
}

submission_spec = create_submission_spec(
    flow_spec,
    label="TiO2 static",
    timestamp="20260629-120000",
    env={},
)


class SuccessfulSubmitRunner:
    def __init__(self):
        self.connected_profile = None
        self.dry_run = None
        self.closed = False

    def connect(self, profile):
        self.connected_profile = profile

    def submit(self, spec, dry_run=False):
        self.dry_run = dry_run
        assert spec is submission_spec
        return JobRecord(
            job_id="123456",
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
            submitted_at="2026-06-29 12:00:00",
            raw_output="SBATCH_RAW_OUT=123456\n",
            status="submitted",
            submission_spec=spec,
            remote_state_path=f"{DEFAULT_LOGS_DIR}/job_123456.json",
        )

    def close(self):
        self.closed = True


successful_runner = SuccessfulSubmitRunner()
success = submit_remote_workflow(
    submission_spec,
    remote_prepared=True,
    runner_factory=lambda: successful_runner,
)

assert successful_runner.connected_profile.host == DEFAULT_REMOTE_HOST
assert successful_runner.dry_run is False
assert successful_runner.closed is True
assert success["status"] == "success"
assert success["title"] == "Submitted"
assert success["stage"] == "Submission"
assert success["job_id"] == "123456"
assert success["queue_status"] == "Submitted"
assert success["job_record"]["status"] == "submitted"


blocked = submit_remote_workflow(
    submission_spec,
    remote_prepared=False,
    runner_factory=lambda: SuccessfulSubmitRunner(),
)

assert blocked["status"] == "failed"
assert blocked["stage"] == "Submission"
assert "Remote preparation" in blocked["reason"]
assert blocked["queue_status"] == "Not submitted"


class SubmissionFailureRunner(SuccessfulSubmitRunner):
    def submit(self, spec, dry_run=False):
        self.dry_run = dry_run
        result = RemoteCommandResult(
            command="sbatch failing command",
            returncode=1,
            stderr=(
                "Traceback (most recent call last):\n"
                '  File "run_job.py", line 1, in <module>\n'
                "RuntimeError: sbatch rejected the job\n"
            ),
        )
        raise RemoteExecutionError(result)


failure_runner = SubmissionFailureRunner()
failure = submit_remote_workflow(
    submission_spec,
    remote_prepared=True,
    runner_factory=lambda: failure_runner,
)

assert failure_runner.dry_run is False
assert failure_runner.closed is True
assert failure["status"] == "failed"
assert failure["stage"] == "Submission"
assert failure["exit_code"] == 1
assert "sbatch rejected the job" in failure["reason"]
assert "Traceback" not in failure["reason"]
assert "run_job.py" not in failure["reason"]
assert "sbatch rejected the job" in failure["stderr"]
assert failure["queue_status"] == "Not submitted"


class SbatchOutputFailureRunner(SuccessfulSubmitRunner):
    def submit(self, spec, dry_run=False):
        self.dry_run = dry_run
        result = RemoteCommandResult(
            command="set -e -o pipefail\nsbatch -p bad --parsable job.sh",
            returncode=1,
            stdout=(
                "Submitting with: sbatch -p bad --parsable job.sh\n"
                "SBATCH_RAW_OUT=sbatch: error: Batch job submission failed: Invalid account or account/partition combination specified\n"
            ),
            stderr="",
        )
        raise RemoteExecutionError(result)


sbatch_failure_runner = SbatchOutputFailureRunner()
sbatch_failure = submit_remote_workflow(
    submission_spec,
    remote_prepared=True,
    runner_factory=lambda: sbatch_failure_runner,
)

assert sbatch_failure_runner.dry_run is False
assert sbatch_failure["status"] == "failed"
assert sbatch_failure["stage"] == "Submission"
assert sbatch_failure["exit_code"] == 1
assert "Invalid account" in sbatch_failure["reason"]
assert "Invalid account" in sbatch_failure["stdout"]
assert sbatch_failure["stderr"] == ""
assert "Submitting with:" not in sbatch_failure["reason"]
assert "sbatch -p bad" not in sbatch_failure["reason"]
assert sbatch_failure["streams_empty"] is False


class EmptyOutputFailureRunner(SuccessfulSubmitRunner):
    def submit(self, spec, dry_run=False):
        self.dry_run = dry_run
        result = RemoteCommandResult(
            command="sbatch failing command",
            returncode=2,
            stdout="",
            stderr="",
        )
        raise RemoteExecutionError(result)


empty_output_failure = submit_remote_workflow(
    submission_spec,
    remote_prepared=True,
    runner_factory=lambda: EmptyOutputFailureRunner(),
)

assert empty_output_failure["status"] == "failed"
assert empty_output_failure["exit_code"] == 2
assert empty_output_failure["stdout"] == ""
assert empty_output_failure["stderr"] == ""
assert empty_output_failure["streams_empty"] is True
assert "No stdout or stderr was returned by sbatch" in empty_output_failure["reason"]

print("remote submission smoke test passed")
