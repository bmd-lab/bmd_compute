from __future__ import annotations

import json
import posixpath
import re
import shlex
import time
from typing import Any, Mapping

from backend.remote import (
    BatchSubmissionRequest,
    BatchSubmissionResult,
    JobRecord,
    RemoteCommandResult,
    RemoteConnectionProfile,
    RemoteExecutionError,
    RemoteJobStatus,
    RemotePathInfo,
    RemoteProcess,
    RemoteRunner,
    RemoteTransferResult,
    RemoteTunnel,
)
from backend.submission import build_submission_command, parse_sbatch_job_id


class ParamikoRemoteRunner(RemoteRunner):
    """
    Paramiko-backed implementation of the notebook's remote submission path.

    Monitoring and result parsing are intentionally not implemented here.
    """

    def __init__(self, client=None, environment: Mapping[str, str] | None = None):
        self.client = client
        self.environment = dict(environment or {})

    def connect(self, profile: RemoteConnectionProfile) -> None:
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=profile.host,
            port=profile.port,
            username=profile.username,
            key_filename=profile.key_file,
            allow_agent=True,
            look_for_keys=True,
            timeout=20,
        )

        transport = client.get_transport()
        if not transport or not transport.is_active():
            client.close()
            raise RuntimeError("SSH transport failed to start.")

        if profile.keepalive_s:
            transport.set_keepalive(profile.keepalive_s)

        self.client = client

    def ensure_available(self) -> None:
        if self.client is None:
            raise RuntimeError("Remote client is not connected.")

        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise RuntimeError("SSH transport is not active.")

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None

    def run(
        self,
        command: str,
        *,
        check: bool = False,
        modules: bool = False,
        export_env: bool = False,
        timeout_s: float | None = None,
    ) -> RemoteCommandResult:
        self.ensure_available()
        parts = ["set -e -o pipefail"]

        if export_env:
            for key in ("VASP_CMD", "JOBFLOW_CONFIG_FILE", "PMG_VASP_PSP_DIR"):
                value = self.environment.get(key)
                if value:
                    parts.append(f"export {key}={shlex.quote(value)}")

        if modules:
            parts.extend(
                [
                    "module purge >/dev/null 2>&1 || true",
                    "module load intel/rocky8-oneAPI-2023 >/dev/null 2>&1 || true",
                    "module load vasp/rocky8-intel-6.4.1 >/dev/null 2>&1 || true",
                ]
            )

        parts.append(command)
        full_command = "\n".join(parts)
        started = time.time()
        stdin, stdout, stderr = self.client.exec_command(
            full_command,
            get_pty=False,
            timeout=timeout_s,
        )
        del stdin
        out = stdout.read().decode("utf-8", "ignore")
        err = stderr.read().decode("utf-8", "ignore")
        returncode = stdout.channel.recv_exit_status()
        result = RemoteCommandResult(
            command=full_command,
            returncode=returncode,
            stdout=out,
            stderr=err,
            elapsed_s=time.time() - started,
        )

        if check:
            result.raise_for_status()

        return result

    def stream(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
    ) -> RemoteCommandResult:
        raise NotImplementedError("Streaming remote commands is outside the submission milestone.")

    def run_python(
        self,
        source: str,
        *,
        python: str,
        env: Mapping[str, str] | None = None,
        check: bool = False,
        timeout_s: float | None = None,
    ) -> RemoteCommandResult:
        env_prefix = ""
        if env:
            env_prefix = " ".join(
                f"{key}={shlex.quote(value)}"
                for key, value in env.items()
            )
            env_prefix += " "

        command = f"{env_prefix}{shlex.quote(python)} - <<'PY'\n{source}\nPY"
        return self.run(command, check=check, timeout_s=timeout_s)

    def stat(self, remote_path: str) -> RemotePathInfo:
        command = (
            "if [ -e {path} ]; then "
            "kind=file; [ -d {path} ] && kind=dir; "
            "size=$(stat -c %s {path} 2>/dev/null || echo 0); "
            "mtime=$(stat -c %Y {path} 2>/dev/null || echo 0); "
            "echo \"$kind|$size|$mtime\"; "
            "else echo MISSING; fi"
        ).format(path=shlex.quote(remote_path))
        result = self.run(command, check=False)
        output = (result.stdout or "").strip()

        if output == "MISSING" or result.returncode != 0:
            return RemotePathInfo(path=remote_path, exists=False)

        kind, size, mtime = (output.split("|", 2) + ["", ""])[:3]
        return RemotePathInfo(
            path=remote_path,
            exists=True,
            kind=kind or None,
            size_bytes=int(size) if str(size).isdigit() else None,
            mtime=float(mtime) if _looks_numeric(mtime) else None,
        )

    def exists(self, remote_path: str) -> bool:
        return self.run(f"test -e {shlex.quote(remote_path)}", check=False).ok

    def is_file(self, remote_path: str) -> bool:
        return self.run(f"test -f {shlex.quote(remote_path)}", check=False).ok

    def is_dir(self, remote_path: str) -> bool:
        return self.run(f"test -d {shlex.quote(remote_path)}", check=False).ok

    def ensure_directory(self, remote_path: str) -> RemotePathInfo:
        self.run(f"mkdir -p {shlex.quote(remote_path)}", check=True)
        return self.stat(remote_path)

    def chmod(self, remote_path: str, mode: int) -> RemotePathInfo:
        self.run(f"chmod {oct(mode)[2:]} {shlex.quote(remote_path)}", check=True)
        return self.stat(remote_path)

    def symlink(self, target: str, link_name: str, *, overwrite: bool = True) -> RemotePathInfo:
        flag = "-sfn" if overwrite else "-sn"
        self.run(f"ln {flag} {shlex.quote(target)} {shlex.quote(link_name)}", check=True)
        return self.stat(link_name)

    def rename(self, source: str, destination: str, *, overwrite: bool = True) -> RemotePathInfo:
        flag = "-f" if overwrite else "-n"
        self.run(f"mv {flag} {shlex.quote(source)} {shlex.quote(destination)}", check=True)
        return self.stat(destination)

    def remove(self, remote_path: str, *, missing_ok: bool = True) -> RemotePathInfo:
        flag = "-f" if missing_ok else ""
        self.run(f"rm {flag} {shlex.quote(remote_path)}", check=not missing_ok)
        return RemotePathInfo(path=remote_path, exists=False)

    def put_text(self, remote_path: str, text: str, *, mode: int = 0o640) -> RemoteTransferResult:
        self.ensure_available()
        parent = posixpath.dirname(remote_path.rstrip("/"))
        if parent:
            self.ensure_directory(parent)

        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as handle:
                handle.write(text)
            sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

        return RemoteTransferResult(
            remote_path=remote_path,
            bytes_transferred=len(text.encode("utf-8")),
            mode=mode,
        )

    def read_text(self, remote_path: str, *, max_bytes: int | None = None) -> str:
        data = self.read_bytes(remote_path, max_bytes=max_bytes)
        return data.decode("utf-8", "ignore")

    def put_bytes(self, remote_path: str, data: bytes, *, mode: int = 0o640) -> RemoteTransferResult:
        self.ensure_available()
        parent = posixpath.dirname(remote_path.rstrip("/"))
        if parent:
            self.ensure_directory(parent)

        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "wb") as handle:
                handle.write(data)
            sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

        return RemoteTransferResult(
            remote_path=remote_path,
            bytes_transferred=len(data),
            mode=mode,
        )

    def read_bytes(self, remote_path: str, *, max_bytes: int | None = None) -> bytes:
        self.ensure_available()
        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "rb") as handle:
                data = handle.read(max_bytes) if max_bytes else handle.read()
        finally:
            sftp.close()
        return data

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        *,
        mode: int | None = None,
    ) -> RemoteTransferResult:
        self.ensure_available()
        parent = posixpath.dirname(remote_path.rstrip("/"))
        if parent:
            self.ensure_directory(parent)

        sftp = self.client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

        return RemoteTransferResult(remote_path=remote_path, local_path=local_path, mode=mode)

    def download_file(self, remote_path: str, local_path: str) -> RemoteTransferResult:
        self.ensure_available()
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()
        return RemoteTransferResult(remote_path=remote_path, local_path=local_path)

    def open_tunnel(
        self,
        *,
        local_port: int,
        remote_host: str,
        remote_port: int,
        local_host: str = "127.0.0.1",
    ) -> RemoteTunnel:
        raise NotImplementedError("Tunnels are outside the submission milestone.")

    def close_tunnel(self, tunnel_id: str) -> None:
        raise NotImplementedError("Tunnels are outside the submission milestone.")

    def close_tunnels(self) -> None:
        raise NotImplementedError("Tunnels are outside the submission milestone.")

    def start_background(
        self,
        command: str,
        *,
        pid_path: str | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> RemoteProcess:
        raise NotImplementedError("Background monitoring is outside the submission milestone.")

    def stop_background(self, process: RemoteProcess | str, *, missing_ok: bool = True) -> None:
        raise NotImplementedError("Background monitoring is outside the submission milestone.")

    def submit_batch(self, request: BatchSubmissionRequest) -> BatchSubmissionResult:
        command = (
            f"sbatch -p {shlex.quote(request.partition)} "
            f"-A {shlex.quote(request.account)} "
            f"-N {int(request.nodes)} "
            f"-n {int(request.ntasks)} "
            f"--mem={int(request.mem_gb)}G "
            f"-t {shlex.quote(request.walltime)} "
            f"{'--parsable ' if request.parsable else ''}"
            f"{shlex.quote(request.script_path)}"
        )
        result = self.run(command, check=False)
        if not result.ok:
            raise RemoteExecutionError(result)

        job_id = parse_sbatch_job_id(result.stdout)
        return BatchSubmissionResult(job_id=job_id, raw_output=result.stdout, command=command)

    def submit(self, submission_spec: dict, dry_run: bool = False) -> JobRecord:
        self.ensure_available()
        self._preflight(submission_spec)

        command = build_submission_command(submission_spec, dry_run=dry_run)
        result = self.run(command, check=False, modules=False, export_env=False)
        if not result.ok:
            raise RemoteExecutionError(result)

        output = result.stdout or ""
        if dry_run:
            return self._job_record(submission_spec, None, output, status="dry_run")

        job_id = parse_sbatch_job_id(output)
        record = self._job_record(submission_spec, job_id, output)
        self._write_remote_job_record(record)
        return record

    def query_job(self, job_id: str) -> RemoteJobStatus:
        raise NotImplementedError("Monitoring is outside the submission milestone.")

    def cancel_job(self, job_id: str) -> RemoteCommandResult:
        raise NotImplementedError("Job cancellation is outside the submission milestone.")

    def read_job_accounting(self, job_id: str) -> RemoteJobStatus:
        raise NotImplementedError("Result accounting is outside the submission milestone.")

    def _preflight(self, submission_spec: dict) -> None:
        structure_spec = submission_spec.get("flow_spec", {}).get("structure", {})
        if structure_spec.get("type") != "path":
            return

        remote_path = structure_spec.get("path", "")
        if not remote_path:
            raise FileNotFoundError("Remote structure path is empty.")

        command = (
            f"test -f {shlex.quote(remote_path)} "
            f"|| test -d {shlex.quote(remote_path)}"
        )
        result = self.run(command, check=False, modules=False, export_env=False)
        if not result.ok:
            raise FileNotFoundError(f"Remote structure path not found: {remote_path}")

    def _job_record(
        self,
        submission_spec: dict,
        job_id: str | None,
        output: str,
        *,
        status: str = "submitted",
    ) -> JobRecord:
        paths = submission_spec["paths"]
        log_paths = {
            "stdout": paths["log_out"],
            "stderr": paths["log_err"],
            "slurm_out": paths["slurm_out"],
            "slurm_err": paths["slurm_err"],
        }
        remote_state_path = (
            posixpath.join(paths["logs_dir"], f"job_{job_id}.json")
            if job_id
            else None
        )

        return JobRecord(
            job_id=job_id,
            run_name=submission_spec["run_name"],
            run_dir=paths["run_dir"],
            remote_script=paths["remote_script"],
            log_paths=log_paths,
            cluster=dict(submission_spec["cluster"]),
            resources=dict(submission_spec["resources"]),
            submitted_at=_now_str(),
            raw_output=output,
            status=status,
            submission_spec=dict(submission_spec),
            remote_state_path=remote_state_path,
        )

    def _write_remote_job_record(self, record: JobRecord) -> None:
        if not record.remote_state_path:
            return

        try:
            payload = json.dumps(record.to_dict(), indent=2)
            self.put_text(record.remote_state_path, payload)
        except Exception:
            # The notebook treated remote state persistence as best-effort.
            return


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _looks_numeric(value: str) -> bool:
    return bool(re.match(r"^-?\d+(\.\d+)?$", str(value or "")))


__all__ = [
    "JobRecord",
    "ParamikoRemoteRunner",
]
