from backend.config import DEFAULT_FLOWS_DIR, DEFAULT_REMOTE_HOST
from backend.remote import JobRecord, RemoteCommandResult, RemoteExecutionError
from backend.remote_preparation import prepare_remote_submission
from backend.submission import create_submission_spec


RUN_NAME = "TiO2-static-20260629-120000"
RUN_DIR = f"{DEFAULT_FLOWS_DIR}/{RUN_NAME}"

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


verified_output = "\n".join(
    [
        "PREP_OK=Remote directories prepared",
        "PREP_OK=Working directory created",
        "PREP_OK=submission.json uploaded",
        "PREP_OK=Execution module uploaded",
        "PREP_OK=run_job.py uploaded",
        "PREP_OK=Submission script written",
        "PREP_OK=Ready for submission",
        "DRY RUN",
    ]
) + "\n"


class SuccessfulRunner:
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
            job_id=None,
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
            raw_output=verified_output,
            status="dry_run",
        )

    def close(self):
        self.closed = True


successful_runner = SuccessfulRunner()
success = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: successful_runner,
)

assert successful_runner.connected_profile.host == DEFAULT_REMOTE_HOST
assert successful_runner.dry_run is True
assert successful_runner.closed is True
assert success["status"] == "success"
assert success["ready_for_submission"] is True
assert success["job_record"]["job_id"] is None
assert success["job_record"]["status"] == "dry_run"
assert [step["label"] for step in success["steps"]][-1] == "Ready for submission"
assert "POTCAR links prepared" not in [step["label"] for step in success["steps"]]
assert all(step["state"] == "complete" for step in success["steps"])


class MissingVerificationRunner(SuccessfulRunner):
    def submit(self, spec, dry_run=False):
        record = super().submit(spec, dry_run=dry_run)
        return JobRecord(
            job_id=record.job_id,
            run_name=record.run_name,
            run_dir=record.run_dir,
            remote_script=record.remote_script,
            log_paths=record.log_paths,
            cluster=record.cluster,
            resources=record.resources,
            submitted_at=record.submitted_at,
            raw_output="DRY RUN\n",
            status=record.status,
        )


missing_runner = MissingVerificationRunner()
missing_verification = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: missing_runner,
)

assert missing_verification["status"] == "failed"
assert missing_verification["ready_for_submission"] is False
assert missing_verification["stage"] == "Remote directories prepared"
assert "without verifying" in missing_verification["reason"]


class ConnectionFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        raise OSError("timed out")

    def close(self):
        self.closed = True


connection_runner = ConnectionFailureRunner()
connection_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: connection_runner,
)

assert connection_runner.closed is True
assert connection_failure["status"] == "failed"
assert connection_failure["ready_for_submission"] is False
assert connection_failure["stage"] == "SSH Connection"
assert connection_failure["reason"] == f"Unable to connect to {DEFAULT_REMOTE_HOST}."
assert "VPN" in connection_failure["suggestion"]
assert connection_failure["steps"][0] == {
    "label": "Remote connection established",
    "state": "failed",
}


class PreparationFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        return None

    def submit(self, spec, dry_run=False):
        result = RemoteCommandResult(
            command="mkdir -p /remote/path",
            returncode=1,
            stderr="mkdir: cannot create directory: Permission denied",
        )
        raise RemoteExecutionError(result)

    def close(self):
        self.closed = True


preparation_runner = PreparationFailureRunner()
preparation_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: preparation_runner,
)

assert preparation_runner.closed is True
assert preparation_failure["status"] == "failed"
assert preparation_failure["stage"] == "Remote Preparation"
assert "denied" in preparation_failure["reason"].lower()
assert "permissions" in preparation_failure["suggestion"].lower()


class VerifiedPreparationFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        return None

    def submit(self, spec, dry_run=False):
        result = RemoteCommandResult(
            command="verified dry run",
            returncode=42,
            stdout=(
                "PREP_OK=Remote directories prepared\n"
                "PREP_FAILED_STAGE=Working directory created\n"
                "PREP_FAILED_REASON=Expected directory does not exist: "
                f"{RUN_DIR}\n"
            ),
        )
        raise RemoteExecutionError(result)

    def close(self):
        self.closed = True


verified_failure_runner = VerifiedPreparationFailureRunner()
verified_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: verified_failure_runner,
)

assert verified_failure_runner.closed is True
assert verified_failure["status"] == "failed"
assert verified_failure["stage"] == "Working directory created"
assert "Expected directory does not exist" in verified_failure["reason"]
assert verified_failure["steps"][-1] == {
    "label": "Working directory created",
    "state": "failed",
}


class RunJobUploadFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        return None

    def submit(self, spec, dry_run=False):
        result = RemoteCommandResult(
            command="verified dry run",
            returncode=42,
            stdout=(
                "PREP_OK=Remote directories prepared\n"
                "PREP_OK=Working directory created\n"
                "PREP_OK=submission.json uploaded\n"
                "PREP_OK=Execution module uploaded\n"
                "PREP_FAILED_STAGE=run_job.py uploaded\n"
                "PREP_FAILED_REASON=Expected file does not exist: "
                f"{RUN_DIR}/run_job.py\n"
            ),
        )
        raise RemoteExecutionError(result)

    def close(self):
        self.closed = True


run_job_failure_runner = RunJobUploadFailureRunner()
run_job_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: run_job_failure_runner,
)

assert run_job_failure_runner.closed is True
assert run_job_failure["status"] == "failed"
assert run_job_failure["stage"] == "run_job.py uploaded"
assert "Expected file does not exist" in run_job_failure["reason"]
assert run_job_failure["steps"][-1] == {
    "label": "run_job.py uploaded",
    "state": "failed",
}


class SubmissionJsonUploadFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        return None

    def submit(self, spec, dry_run=False):
        result = RemoteCommandResult(
            command="verified dry run",
            returncode=42,
            stdout=(
                "PREP_OK=Remote directories prepared\n"
                "PREP_OK=Working directory created\n"
                "PREP_FAILED_STAGE=submission.json uploaded\n"
                "PREP_FAILED_REASON=Expected file does not exist: "
                f"{RUN_DIR}/submission.json\n"
            ),
        )
        raise RemoteExecutionError(result)

    def close(self):
        self.closed = True


submission_json_failure_runner = SubmissionJsonUploadFailureRunner()
submission_json_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: submission_json_failure_runner,
)

assert submission_json_failure_runner.closed is True
assert submission_json_failure["status"] == "failed"
assert submission_json_failure["stage"] == "submission.json uploaded"
assert "Expected file does not exist" in submission_json_failure["reason"]
assert submission_json_failure["steps"][-1] == {
    "label": "submission.json uploaded",
    "state": "failed",
}


class ExecutionModuleUploadFailureRunner:
    def __init__(self):
        self.closed = False

    def connect(self, profile):
        return None

    def submit(self, spec, dry_run=False):
        result = RemoteCommandResult(
            command="verified dry run",
            returncode=42,
            stdout=(
                "PREP_OK=Remote directories prepared\n"
                "PREP_OK=Working directory created\n"
                "PREP_OK=submission.json uploaded\n"
                "PREP_FAILED_STAGE=Execution module uploaded\n"
                "PREP_FAILED_REASON=Expected file does not exist: "
                f"{RUN_DIR}/backend/execution.py\n"
            ),
        )
        raise RemoteExecutionError(result)

    def close(self):
        self.closed = True


execution_module_failure_runner = ExecutionModuleUploadFailureRunner()
execution_module_failure = prepare_remote_submission(
    submission_spec,
    runner_factory=lambda: execution_module_failure_runner,
)

assert execution_module_failure_runner.closed is True
assert execution_module_failure["status"] == "failed"
assert execution_module_failure["stage"] == "Execution module uploaded"
assert "Expected file does not exist" in execution_module_failure["reason"]
assert execution_module_failure["steps"][-1] == {
    "label": "Execution module uploaded",
    "state": "failed",
}

print("remote preparation smoke test passed")
