from backend.paramiko_remote import JobRecord, ParamikoRemoteRunner
from backend.remote import RemoteCommandResult
from backend.submission import create_submission_spec, parse_sbatch_job_id


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
assert record.run_name == "TiO2-static-20260629-120000"
assert record.run_dir == "/bmd-db/lee/flows/TiO2-static-20260629-120000"
assert record.remote_script == "/bmd-db/lee/flows/TiO2-static-20260629-120000.sbatch.sh"
assert record.log_paths["stdout"] == "/bmd-db/lee/logs/TiO2-static-20260629-120000.out"
assert record.log_paths["slurm_out"] == "/bmd-db/lee/logs/TiO2-static-20260629-120000.slurm.out"
assert record.remote_state_path == "/bmd-db/lee/logs/job_123456.json"

assert runner.commands[0] == "test -f /remote/POSCAR || test -d /remote/POSCAR"
submit_command = runner.commands[1]
assert "mkdir -p" in submit_command
assert "ln -sfn /bmd-db/lee/potcars/PBE_64" in submit_command
assert "cat > /bmd-db/lee/flows/TiO2-static-20260629-120000.sbatch.sh" in submit_command
assert "sbatch -p power-leeburton -A power-leeburton-users" in submit_command
assert "-N 1 -n 24 --mem=120G -t 72:00:00 --parsable" in submit_command

assert runner.remote_writes
assert runner.remote_writes[0][0] == "/bmd-db/lee/logs/job_123456.json"

dry_runner = RecordingRunner()
dry_record = dry_runner.submit(submission_spec, dry_run=True)

assert isinstance(dry_record, JobRecord)
assert dry_record.job_id is None
assert dry_record.status == "dry_run"
assert dry_record.run_name == "TiO2-static-20260629-120000"
assert dry_record.run_dir == "/bmd-db/lee/flows/TiO2-static-20260629-120000"
assert dry_record.remote_script == "/bmd-db/lee/flows/TiO2-static-20260629-120000.sbatch.sh"
assert dry_record.remote_state_path is None
assert dry_record.raw_output == "DRY RUN\n"

assert dry_runner.commands[0] == "test -f /remote/POSCAR || test -d /remote/POSCAR"
dry_command = dry_runner.commands[1]
assert "mkdir -p" in dry_command
assert "ln -sfn /bmd-db/lee/potcars/PBE_64" in dry_command
assert "cat > /bmd-db/lee/flows/TiO2-static-20260629-120000.sbatch.sh" in dry_command
assert "PREP_FAILED_STAGE=$1" in dry_command
assert "verify_dir 'Working directory created' /bmd-db/lee/flows/TiO2-static-20260629-120000" in dry_command
assert "verify_symlink 'POTCAR links prepared' /bmd-db/lee/potcars/PBE_64" in dry_command
assert "verify_file \"Submission script written\" /bmd-db/lee/flows/TiO2-static-20260629-120000.sbatch.sh" in dry_command
assert 'prep_ok "Ready for submission"' in dry_command
assert "echo DRY RUN" in dry_command
assert "Submitting with: sbatch" not in dry_command
assert "out=$(sbatch" not in dry_command
assert not dry_runner.remote_writes

assert parse_sbatch_job_id("Submitted batch job 42") == "42"
assert parse_sbatch_job_id("SBATCH_RAW_OUT=77") == "77"

print("paramiko remote submit smoke test passed")
