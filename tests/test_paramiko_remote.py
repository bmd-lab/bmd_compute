from backend.config import (
    DEFAULT_ACCOUNT,
    DEFAULT_FLOWS_DIR,
    DEFAULT_LOGS_DIR,
    DEFAULT_PARTITION,
    DEFAULT_POTCAR_DIR,
    DEFAULT_RESOURCES,
)
from backend.monitoring import MonitoringStageError
from backend.paramiko_remote import JobRecord, ParamikoRemoteRunner
from backend.remote import RemoteCommandResult
from backend.submission import create_submission_spec, parse_sbatch_job_id


RUN_NAME = "TiO2-static-20260629-120000"
RUN_DIR = f"{DEFAULT_FLOWS_DIR}/{RUN_NAME}"
REMOTE_SCRIPT = f"{DEFAULT_FLOWS_DIR}/{RUN_NAME}.sbatch.sh"
LOG_OUT = f"{DEFAULT_LOGS_DIR}/{RUN_NAME}.out"
SLURM_OUT = f"{DEFAULT_LOGS_DIR}/{RUN_NAME}.slurm.out"
POTCAR_TARGET = f"{DEFAULT_POTCAR_DIR}/PBE_64"
SUBMISSION_JSON = f"{RUN_DIR}/submission.json"
BACKEND_DIR = f"{RUN_DIR}/backend"
RUN_JOB = f"{RUN_DIR}/run_job.py"


class RecordingRunner(ParamikoRemoteRunner):
    def __init__(self):
        super().__init__(client=object())
        self.commands = []
        self.remote_writes = []

    def ensure_available(self):
        return None

    def run(
        self,
        command,
        *,
        check=False,
        modules=False,
        export_env=False,
        timeout_s=None,
    ):
        self.commands.append(command)
        if command.startswith("test -f"):
            return RemoteCommandResult(command=command, returncode=0)
        if "echo DRY RUN" in command:
            return RemoteCommandResult(command=command, returncode=0, stdout="DRY RUN\n")
        return RemoteCommandResult(
            command=command,
            returncode=0,
            stdout="Submitting with: sbatch ...\nSBATCH_RAW_OUT=123456\n",
        )

    def put_text(self, remote_path, text, *, mode=0o640):
        self.remote_writes.append((remote_path, text, mode))


flow_spec = {
    "workflow": "static",
    "potcar_functional": "PBE_64",
    "kpoints": None,
    "incar": {},
    "structure": {"type": "path", "path": "/remote/POSCAR"},
}

submission_spec = create_submission_spec(
    flow_spec,
    label="TiO2 static",
    timestamp="20260629-120000",
    env={},
)

runner = RecordingRunner()
record = runner.submit(submission_spec)

assert isinstance(record, JobRecord)
assert record.job_id == "123456"
assert record.status == "submitted"
assert record.run_name == RUN_NAME
assert record.run_dir == RUN_DIR
assert record.remote_script == REMOTE_SCRIPT
assert record.log_paths["stdout"] == LOG_OUT
assert record.log_paths["slurm_out"] == SLURM_OUT
assert record.remote_state_path == f"{DEFAULT_LOGS_DIR}/job_123456.json"

assert runner.commands[0] == "test -f /remote/POSCAR || test -d /remote/POSCAR"
submit_command = runner.commands[1]
assert "mkdir -p" in submit_command
assert f"ln -sfn {POTCAR_TARGET}" in submit_command
assert f"cat > {SUBMISSION_JSON} <<'JSON'" in submit_command
assert f"test -f {SUBMISSION_JSON}" in submit_command
assert f"mkdir -p {BACKEND_DIR}" in submit_command
assert f"cat > {BACKEND_DIR}/execution.py <<'PY'" in submit_command
assert f"test -f {BACKEND_DIR}/execution.py" in submit_command
assert f"cat > {BACKEND_DIR}/parser.py <<'PY'" in submit_command
assert f"test -f {BACKEND_DIR}/parser.py" in submit_command
assert f"cat > {BACKEND_DIR}/workflows.py <<'PY'" in submit_command
assert f"test -f {BACKEND_DIR}/workflows.py" in submit_command
assert f"cat > {RUN_JOB} <<'PY'" in submit_command
assert f"test -f {RUN_JOB}" in submit_command
assert f"cat > {REMOTE_SCRIPT}" in submit_command
assert "cat > run_job.py <<'PY'" not in submit_command
assert "__SPEC_JSON__" not in submit_command
assert "json.load(handle)" in submit_command
assert "from backend.execution import run_submission" in submit_command
assert f"export BMD_SUBMISSION_SPEC={SUBMISSION_JSON}" in submit_command
assert f"Missing submission.json at {SUBMISSION_JSON}" in submit_command
assert f"Missing execution module at {BACKEND_DIR}/execution.py" in submit_command
assert f"Missing backend module parser.py at {BACKEND_DIR}/parser.py" in submit_command
assert f"Missing backend module workflows.py at {BACKEND_DIR}/workflows.py" in submit_command
assert f"Missing run_job.py at {RUN_JOB}" in submit_command
assert f"sbatch -p {DEFAULT_PARTITION} -A {DEFAULT_ACCOUNT}" in submit_command
assert (
    f"-N {DEFAULT_RESOURCES['nodes']} "
    f"-n {DEFAULT_RESOURCES['ntasks']} "
    f"--mem={DEFAULT_RESOURCES['mem_gb']}G "
    f"-t {DEFAULT_RESOURCES['walltime']} --parsable"
) in submit_command

assert runner.remote_writes
assert runner.remote_writes[0][0] == f"{DEFAULT_LOGS_DIR}/job_123456.json"

dry_runner = RecordingRunner()
dry_record = dry_runner.submit(submission_spec, dry_run=True)

assert isinstance(dry_record, JobRecord)
assert dry_record.job_id is None
assert dry_record.status == "dry_run"
assert dry_record.run_name == RUN_NAME
assert dry_record.run_dir == RUN_DIR
assert dry_record.remote_script == REMOTE_SCRIPT
assert dry_record.remote_state_path is None
assert dry_record.raw_output == "DRY RUN\n"

assert dry_runner.commands[0] == "test -f /remote/POSCAR || test -d /remote/POSCAR"
dry_command = dry_runner.commands[1]
assert "mkdir -p" in dry_command
assert f"ln -sfn {POTCAR_TARGET}" in dry_command
assert f"cat > {REMOTE_SCRIPT}" in dry_command
assert "PREP_FAILED_STAGE=$1" in dry_command
assert f"verify_dir 'Working directory created' {RUN_DIR}" in dry_command
assert f"cat > {SUBMISSION_JSON} <<'JSON'" in dry_command
assert f"verify_file \"submission.json uploaded\" {SUBMISSION_JSON}" in dry_command
assert 'prep_ok "submission.json uploaded"' in dry_command
assert f"cat > {BACKEND_DIR}/execution.py <<'PY'" in dry_command
assert f"verify_file 'Execution module uploaded' {BACKEND_DIR}/execution.py" in dry_command
assert f"cat > {BACKEND_DIR}/parser.py <<'PY'" in dry_command
assert f"verify_file 'Execution module uploaded' {BACKEND_DIR}/parser.py" in dry_command
assert f"cat > {BACKEND_DIR}/workflows.py <<'PY'" in dry_command
assert f"verify_file 'Execution module uploaded' {BACKEND_DIR}/workflows.py" in dry_command
assert 'prep_ok "Execution module uploaded"' in dry_command
assert f"cat > {RUN_JOB} <<'PY'" in dry_command
assert f"verify_file \"run_job.py uploaded\" {RUN_JOB}" in dry_command
assert 'prep_ok "run_job.py uploaded"' in dry_command
assert "__SPEC_JSON__" not in dry_command
assert f"verify_symlink 'POTCAR links prepared' {POTCAR_TARGET}" in dry_command
assert f"verify_file \"Submission script written\" {REMOTE_SCRIPT}" in dry_command
assert 'prep_ok "Ready for submission"' in dry_command
assert "echo DRY RUN" in dry_command
assert "Submitting with: sbatch" not in dry_command
assert "out=$(sbatch" not in dry_command
assert not dry_runner.remote_writes

assert parse_sbatch_job_id("Submitted batch job 42") == "42"
assert parse_sbatch_job_id("SBATCH_RAW_OUT=77") == "77"


class QueryRunner(ParamikoRemoteRunner):
    def __init__(self, outputs):
        super().__init__(client=object())
        self.outputs = outputs
        self.commands = []
        self.timeout_values = []

    def ensure_available(self):
        return None

    def run(
        self,
        command,
        *,
        check=False,
        modules=False,
        export_env=False,
        timeout_s=None,
    ):
        self.commands.append(command)
        self.timeout_values.append(timeout_s)
        if "/usr/bin/scontrol" in command:
            stdout = self.outputs.get("scontrol", "")
        elif "/usr/bin/squeue" in command:
            stdout = self.outputs.get("squeue", "")
        elif "JobIDRaw,State,ExitCode,JobName,StdOut,WorkDir" in command:
            stdout = self.outputs.get("sacct", "")
        elif "JobID,JobName%30,State,Elapsed,Start,End,Partition%20" in command:
            stdout = self.outputs.get("sacct_brief", "")
        else:
            stdout = ""
        return RemoteCommandResult(command=command, returncode=0, stdout=stdout)


running_query_runner = QueryRunner(
    {
        "scontrol": "JobId=123456 JobName=TiO2-static JobState=RUNNING StdOut=/logs/job.out WorkDir=/flows/run\n",
        "squeue": "RUNNING\n",
    }
)
running_status = running_query_runner.query_job("123456.batch")

assert running_status.job_id == "123456"
assert running_status.state == "RUNNING"
assert running_status.raw["summary"] == "RUNNING"
assert running_status.stdout_path == "/logs/job.out"
assert running_status.workdir == "/flows/run"
assert running_status.job_name == "TiO2-static"
assert len(running_query_runner.commands) == 2
assert all("/usr/bin/sacct" not in command for command in running_query_runner.commands)
assert all("timeout 2s" not in command for command in running_query_runner.commands)
assert running_query_runner.timeout_values == [None, None]

completed_query_runner = QueryRunner(
    {
        "scontrol": "",
        "squeue": "",
        "sacct": "123456|COMPLETED|0:0|TiO2-static|/logs/final.out|/flows/run\n",
        "sacct_brief": "123456|TiO2-static|COMPLETED|00:02:10|2026-07-14T10:00|2026-07-14T10:02|leeburton-pool\n",
    }
)
completed_status = completed_query_runner.query_job("123456")

assert completed_status.job_id == "123456"
assert completed_status.state == "COMPLETED"
assert completed_status.exit_code == "0:0"
assert completed_status.raw["summary"] == "SUCCESS"
assert completed_status.stdout_path == "/logs/final.out"
assert "TiO2-static" in completed_status.raw["brief"]
assert len(completed_query_runner.commands) == 4
assert any("/usr/bin/sacct" in command for command in completed_query_runner.commands)
assert all("timeout 2s" not in command for command in completed_query_runner.commands)
assert completed_query_runner.timeout_values == [None, None, None, None]


class FailingSqueueRunner(QueryRunner):
    def run(
        self,
        command,
        *,
        check=False,
        modules=False,
        export_env=False,
        timeout_s=None,
    ):
        self.commands.append(command)
        self.timeout_values.append(timeout_s)
        if "/usr/bin/squeue" in command:
            raise TimeoutError("squeue timeout")
        return RemoteCommandResult(command=command, returncode=0, stdout="")


failing_squeue_runner = FailingSqueueRunner({"scontrol": ""})
try:
    failing_squeue_runner.query_job("123456")
except MonitoringStageError as exc:
    assert exc.stage == "squeue"
    assert "/usr/bin/squeue" in exc.command
    assert "timeout 2s" not in exc.command
    assert exc.stdout == ""
    assert exc.stderr == ""
    assert exc.exception_text == "squeue timeout"
else:
    raise AssertionError("MonitoringStageError was not raised")

print("paramiko remote submit smoke test passed")
