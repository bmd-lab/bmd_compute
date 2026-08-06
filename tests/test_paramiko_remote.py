from backend.config import (
    DEFAULT_ACCOUNT,
    DEFAULT_FLOWS_DIR,
    DEFAULT_LOGS_DIR,
    DEFAULT_PARTITION,
    DEFAULT_POTCAR_DIR,
    DEFAULT_RESOURCES,
    POTCAR_LINK_MAP,
)
from backend.monitoring import MonitoringStageError
from backend.paramiko_remote import (
    JobRecord,
    ParamikoRemoteRunner,
    _identity_files_from_ssh_config,
    _openssh_host_patterns_match,
    _parse_openssh_config_output,
    _paramiko_connect_details_from_ssh_options,
)
from backend.remote import RemoteCommandResult, RemoteConnectionProfile
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


ssh_g_options = _parse_openssh_config_output(
    """
hostname powerslurm-login.tau.ac.il
user bmdguest
port 22
identityfile ~/.ssh/bmd_guest_ed25519
proxycommand none
"""
)
assert ssh_g_options["hostname"] == "powerslurm-login.tau.ac.il"
assert ssh_g_options["user"] == "bmdguest"
assert ssh_g_options["port"] == "22"
identity_file = _identity_files_from_ssh_config(ssh_g_options)
assert identity_file.replace("\\", "/").endswith("/.ssh/bmd_guest_ed25519")

quoted_identity_options = _parse_openssh_config_output(
    'identityfile "C:/Users/lalbu/.ssh/key with spaces"\n'
)
assert _identity_files_from_ssh_config(quoted_identity_options).replace("\\", "/").endswith(
    "/.ssh/key with spaces"
)


class FakeParamikoModule:
    class ProxyCommand:
        def __init__(self, command):
            self.command = command


profile = RemoteConnectionProfile(
    host="powerslurm-bmdguest",
    username="bmdguest",
    port=22,
    key_file="C:/explicit/key",
    ssh_config_host="powerslurm-bmdguest",
)

connect_kwargs, connect_diagnostics = _paramiko_connect_details_from_ssh_options(
    profile,
    "powerslurm-bmdguest",
    {
        "hostname": "powerslurm-login.tau.ac.il",
        "user": "cluster-user",
        "port": "2222",
        "identityfile": ["C:/Users/lalbu/.ssh/bmd_guest_ed25519"],
        "proxycommand": "ssh jump-host nc powerslurm-login.tau.ac.il 2222",
    },
    {
        "source": "openssh ssh -G",
        "host_entry_found": True,
        "loaded_config_files": ["C:/Users/lalbu/.ssh/config"],
        "matching_host_patterns": ["powerslurm-bmdguest"],
    },
    FakeParamikoModule,
)
assert connect_kwargs["hostname"] == "powerslurm-login.tau.ac.il"
assert connect_kwargs["username"] == "cluster-user"
assert connect_kwargs["port"] == 2222
assert connect_kwargs["key_filename"].replace("\\", "/").endswith(
    "/.ssh/bmd_guest_ed25519"
)
assert connect_kwargs["sock"].command == "ssh jump-host nc powerslurm-login.tau.ac.il 2222"
assert connect_diagnostics["ssh_config_applied"] is True
assert connect_diagnostics["resolved_hostname"] == "powerslurm-login.tau.ac.il"
assert connect_diagnostics["resolved_username"] == "cluster-user"
assert connect_diagnostics["resolved_port"] == 2222
assert connect_diagnostics["ssh_config_lookup"]["loaded_config_files"] == [
    "C:/Users/lalbu/.ssh/config"
]
assert connect_diagnostics["ssh_config_lookup"]["host_entry_found"] is True

fallback_kwargs, fallback_diagnostics = _paramiko_connect_details_from_ssh_options(
    profile,
    "powerslurm-bmdguest",
    {
        "hostname": "powerslurm-bmdguest",
        "user": "local-dev-user",
        "port": "22",
        "identityfile": ["~/.ssh/id_ed25519"],
    },
    {
        "source": "openssh ssh -G",
        "host_entry_found": False,
        "loaded_config_files": [],
        "matching_host_patterns": [],
    },
    FakeParamikoModule,
)
assert fallback_kwargs["hostname"] == "powerslurm-bmdguest"
assert fallback_kwargs["username"] == "bmdguest"
assert fallback_kwargs["port"] == 22
assert fallback_kwargs["key_filename"] == "C:/explicit/key"
assert "sock" not in fallback_kwargs
assert fallback_diagnostics["ssh_config_applied"] is False

included_config_kwargs, included_config_diagnostics = _paramiko_connect_details_from_ssh_options(
    profile,
    "powerslurm-bmdguest",
    {
        "hostname": "powerslurm-login.tau.ac.il",
        "user": "included-user",
        "port": "22",
    },
    {
        "source": "openssh ssh -G",
        "host_entry_found": False,
        "loaded_config_files": [],
        "matching_host_patterns": [],
    },
    FakeParamikoModule,
)
assert included_config_kwargs["hostname"] == "powerslurm-login.tau.ac.il"
assert included_config_kwargs["username"] == "included-user"
assert included_config_diagnostics["ssh_config_applied"] is True

assert _openssh_host_patterns_match("powerslurm-bmdguest", ["powerslurm-*"])
assert not _openssh_host_patterns_match(
    "powerslurm-bmdguest",
    ["powerslurm-*", "!powerslurm-bmdguest"],
)


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
assert f"ln -sfn {POTCAR_TARGET}" not in submit_command
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
assert f"test -f {REMOTE_SCRIPT}" in submit_command
assert f"SBATCH_SCRIPT_PATH={REMOTE_SCRIPT}" in submit_command
assert f"rm -f {REMOTE_SCRIPT}" not in submit_command
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
assert f"ln -sfn {POTCAR_TARGET}" not in dry_command
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
assert f"verify_symlink 'POTCAR links prepared' {POTCAR_TARGET}" not in dry_command
assert 'prep_ok "POTCAR links prepared"' not in dry_command
assert f"verify_file \"Submission script written\" {REMOTE_SCRIPT}" in dry_command
assert 'prep_ok "Ready for submission"' in dry_command
assert "echo DRY RUN" in dry_command
assert "Submitting with: sbatch" not in dry_command
assert "out=$(sbatch" not in dry_command
assert not dry_runner.remote_writes

private_potcars_dir = "/private/bmd-potcars"
private_target = f"{private_potcars_dir}/PBE_64"
private_link = f"{private_potcars_dir}/{POTCAR_LINK_MAP['PBE_64'][0]}"
private_submission_spec = create_submission_spec(
    flow_spec,
    label="TiO2 static",
    timestamp="20260629-120000",
    env={},
    potcars_dir=private_potcars_dir,
)

private_runner = RecordingRunner()
private_runner.submit(private_submission_spec)
private_submit_command = private_runner.commands[1]
assert f"ln -sfn {private_target}" in private_submit_command
assert private_link in private_submit_command

private_dry_runner = RecordingRunner()
private_dry_runner.submit(private_submission_spec, dry_run=True)
private_dry_command = private_dry_runner.commands[1]
assert f"ln -sfn {private_target}" in private_dry_command
assert f"verify_symlink 'POTCAR links prepared' {private_target}" in private_dry_command
assert private_link in private_dry_command
assert 'prep_ok "POTCAR links prepared"' in private_dry_command

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
            key = "scontrol"
            stdout = self.outputs.get("scontrol", "")
        elif "/usr/bin/squeue" in command:
            key = "squeue"
            stdout = self.outputs.get("squeue", "")
        elif "JobIDRaw,State,ExitCode,JobName,StdOut,WorkDir" in command:
            key = "sacct"
            stdout = self.outputs.get("sacct", "")
        elif "JobID,JobName%30,State,Elapsed,Start,End,Partition%20" in command:
            key = "sacct_brief"
            stdout = self.outputs.get("sacct_brief", "")
        else:
            key = ""
            stdout = ""
        return RemoteCommandResult(
            command=command,
            returncode=self.outputs.get("returncodes", {}).get(key, 0),
            stdout=stdout,
            stderr=self.outputs.get("stderr", {}).get(key, ""),
        )


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

completed_after_scontrol_miss_runner = QueryRunner(
    {
        "scontrol": "",
        "squeue": "",
        "sacct": "123456|COMPLETED|0:0|TiO2-static|/logs/final.out|/flows/run\n",
        "sacct_brief": "123456|TiO2-static|COMPLETED|00:02:10|2026-07-14T10:00|2026-07-14T10:02|leeburton-pool\n",
        "returncodes": {"scontrol": 1},
        "stderr": {"scontrol": "slurm_load_jobs error: Invalid job id specified\n"},
    }
)
completed_after_scontrol_miss = completed_after_scontrol_miss_runner.query_job("123456")

assert completed_after_scontrol_miss.state == "COMPLETED"
assert completed_after_scontrol_miss.raw["summary"] == "SUCCESS"
assert completed_after_scontrol_miss.raw["scontrol_stderr"] == "slurm_load_jobs error: Invalid job id specified"


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
