from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from backend.config import MODULES
from backend.monitoring import (
    MonitoringStageError,
    remote_job_status_from_slurm_outputs,
    strip_job_id,
)
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
from backend.submission import parse_sbatch_job_id, remote_preparation_file_groups


LOGGER = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_S = 20
DEFAULT_REMOTE_COMMAND_TIMEOUT_S = 60
DEFAULT_MONITOR_COMMAND_TIMEOUT_S = 30
DEFAULT_SFTP_TIMEOUT_S = 120


def _paramiko_connect_kwargs(profile: RemoteConnectionProfile, paramiko_module) -> dict:
    kwargs, _diagnostics = _paramiko_connect_details(profile, paramiko_module)
    return kwargs


def _paramiko_connect_details(profile: RemoteConnectionProfile, paramiko_module) -> tuple[dict, dict]:
    ssh_config_host = profile.ssh_config_host or profile.host
    ssh_options, lookup_diagnostics = _resolve_ssh_config(ssh_config_host, paramiko_module)

    return _paramiko_connect_details_from_ssh_options(
        profile,
        ssh_config_host,
        ssh_options,
        lookup_diagnostics,
        paramiko_module,
    )


def _paramiko_connect_details_from_ssh_options(
    profile: RemoteConnectionProfile,
    ssh_config_host: str,
    ssh_options: Mapping[str, Any],
    lookup_diagnostics: Mapping[str, Any],
    paramiko_module,
) -> tuple[dict, dict]:
    use_ssh_config = _ssh_config_resolution_applies(
        ssh_config_host,
        ssh_options,
        lookup_diagnostics,
    )

    hostname = (
        _clean_ssh_value(ssh_options.get("hostname"))
        if use_ssh_config
        else None
    ) or profile.host
    port = int(
        (
            _clean_ssh_value(ssh_options.get("port"))
            if use_ssh_config
            else None
        )
        or profile.port
    )
    username = (
        _clean_ssh_value(ssh_options.get("user"))
        if use_ssh_config
        else None
    ) or profile.username
    key_filename = (
        _identity_files_from_ssh_config(ssh_options)
        if use_ssh_config
        else None
    ) or profile.key_file
    proxy_command = (
        _clean_ssh_value(ssh_options.get("proxycommand"))
        if use_ssh_config
        else None
    )

    kwargs = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "key_filename": key_filename,
        "allow_agent": True,
        "look_for_keys": True,
        "timeout": DEFAULT_CONNECT_TIMEOUT_S,
        "banner_timeout": DEFAULT_CONNECT_TIMEOUT_S,
        "auth_timeout": DEFAULT_CONNECT_TIMEOUT_S,
        "channel_timeout": DEFAULT_REMOTE_COMMAND_TIMEOUT_S,
    }

    if proxy_command and str(proxy_command).lower() != "none":
        kwargs["sock"] = paramiko_module.ProxyCommand(proxy_command)

    diagnostics = {
        "ssh_config_host": ssh_config_host,
        "hostname": hostname,
        "username": username,
        "port": port,
        "key_filename": key_filename,
        "resolved_hostname": hostname,
        "resolved_username": username,
        "resolved_port": port,
        "resolved_identity_file": key_filename,
        "key_file_exists": _key_file_exists(key_filename),
        "proxy_command": proxy_command if proxy_command and str(proxy_command).lower() != "none" else None,
        "ssh_config_applied": use_ssh_config,
        "ssh_config_lookup": lookup_diagnostics,
    }

    return kwargs, diagnostics


def _ssh_config_resolution_applies(
    host: str,
    ssh_options: Mapping[str, Any],
    lookup_diagnostics: Mapping[str, Any],
) -> bool:
    if lookup_diagnostics.get("host_entry_found"):
        return True

    resolved_hostname = _clean_ssh_value(ssh_options.get("hostname"))
    return bool(resolved_hostname and resolved_hostname != host)


def _resolve_ssh_config(host: str, paramiko_module) -> tuple[dict, dict]:
    matching_host_patterns = _matching_host_patterns_in_loaded_configs(host)
    diagnostics = {
        "loaded_config_files": _existing_ssh_config_paths(),
        "host_entry_found": bool(matching_host_patterns),
        "matching_host_patterns": matching_host_patterns,
        "source": None,
        "ssh_g_returncode": None,
        "ssh_g_stderr": "",
    }

    ssh_g_options, ssh_g_metadata = _lookup_ssh_config_with_openssh(host)
    diagnostics.update(ssh_g_metadata)
    if ssh_g_options:
        diagnostics["source"] = "openssh ssh -G"
        return ssh_g_options, diagnostics

    paramiko_options = _lookup_ssh_config_with_paramiko(host, paramiko_module)
    diagnostics["source"] = "paramiko SSHConfig" if paramiko_options else "fallback profile"
    return paramiko_options, diagnostics


def _lookup_ssh_config_with_openssh(host: str) -> tuple[dict, dict]:
    try:
        result = subprocess.run(
            ["ssh", "-G", host],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return {}, {
            "ssh_g_returncode": None,
            "ssh_g_stderr": str(exc),
        }

    metadata = {
        "ssh_g_returncode": result.returncode,
        "ssh_g_stderr": (result.stderr or "").strip(),
    }
    if result.returncode != 0:
        return {}, metadata

    return _parse_openssh_config_output(result.stdout), metadata


def _parse_openssh_config_output(output: str) -> dict:
    options: dict[str, Any] = {}
    identity_files = []

    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        key, separator, value = line.partition(" ")
        if not separator:
            continue

        key = key.lower()
        value = _clean_ssh_value(value)
        if key == "identityfile":
            if value and value.lower() != "none":
                identity_files.append(value)
        elif key in {"hostname", "user", "port", "proxycommand"}:
            options[key] = value

    if identity_files:
        options["identityfile"] = identity_files

    return options


def _lookup_ssh_config_with_paramiko(host: str, paramiko_module) -> dict:
    ssh_config = paramiko_module.SSHConfig()
    parsed = False
    for config_path in _ssh_config_paths():
        if not config_path.exists():
            continue
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                ssh_config.parse(handle)
        except Exception:
            continue
        parsed = True

    if not parsed:
        return {}

    return dict(ssh_config.lookup(host))


def _existing_ssh_config_paths() -> list[str]:
    return [
        str(path)
        for path in _ssh_config_paths()
        if path.exists()
    ]


def _host_entry_found_in_loaded_configs(host: str) -> bool:
    return bool(_matching_host_patterns_in_loaded_configs(host))


def _matching_host_patterns_in_loaded_configs(host: str) -> list[str]:
    host_pattern = re.compile(r"^\s*Host\s+(.+?)\s*$", re.IGNORECASE)
    matches = []
    for config_path in _ssh_config_paths():
        if not config_path.exists():
            continue

        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        for line in lines:
            match = host_pattern.match(line)
            if not match:
                continue
            patterns = match.group(1).split()
            if _openssh_host_patterns_match(host, patterns):
                matches.extend(patterns)

    return matches


def _openssh_host_patterns_match(host: str, patterns: list[str]) -> bool:
    import fnmatch

    normalized_host = str(host or "").lower()
    positive_match = False
    for pattern in patterns:
        normalized_pattern = str(pattern or "").strip().lower()
        if not normalized_pattern:
            continue
        negated = normalized_pattern.startswith("!")
        if negated:
            normalized_pattern = normalized_pattern[1:]
        if fnmatch.fnmatchcase(normalized_host, normalized_pattern):
            if negated:
                return False
            positive_match = True

    return positive_match


def _ssh_config_paths() -> tuple[Path, ...]:
    candidates = [
        Path.home() / ".ssh" / "config",
        Path(os.path.expanduser("~/.ssh/config")),
    ]

    for env_name in ("HOME", "USERPROFILE"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / ".ssh" / "config")

    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        candidates.append(Path(program_data) / "ssh" / "ssh_config")

    candidates.append(Path("/etc/ssh/ssh_config"))

    unique = []
    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        unique.append(path)
        seen.add(key)

    return tuple(unique)


def _identity_files_from_ssh_config(ssh_options: Mapping[str, Any]) -> str | list[str] | None:
    identity_file = ssh_options.get("identityfile")
    if not identity_file:
        return None

    if isinstance(identity_file, str):
        identity_files = [identity_file]
    else:
        identity_files = list(identity_file)

    expanded = [
        os.path.abspath(os.path.expanduser(_clean_ssh_value(path)))
        for path in identity_files
        if _clean_ssh_value(path) and _clean_ssh_value(path).lower() != "none"
    ]
    if not expanded:
        return None
    if len(expanded) == 1:
        return expanded[0]
    return expanded


def _clean_ssh_value(value) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _key_file_exists(key_filename) -> bool | list[dict[str, bool]]:
    if not key_filename:
        return False

    if isinstance(key_filename, str):
        return os.path.exists(os.path.expanduser(key_filename))

    return [
        {
            "path": str(path),
            "exists": os.path.exists(os.path.expanduser(str(path))),
        }
        for path in key_filename
    ]


def _close_quietly(resource) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _set_timeout_quietly(resource, timeout_s: float | None) -> None:
    if timeout_s is None:
        return

    settimeout = getattr(resource, "settimeout", None)
    if callable(settimeout):
        try:
            settimeout(timeout_s)
        except Exception:
            pass


def _sftp_channel(sftp):
    get_channel = getattr(sftp, "get_channel", None)
    if callable(get_channel):
        try:
            return get_channel()
        except Exception:
            return None
    return None


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

        self.close()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs, diagnostics = _paramiko_connect_details(profile, paramiko)
        LOGGER.debug("Paramiko connect diagnostics: %s", json.dumps(diagnostics, indent=2))
        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            _close_quietly(client)
            _close_quietly(connect_kwargs.get("sock"))
            setattr(exc, "ssh_diagnostics", diagnostics)
            if hasattr(exc, "add_note"):
                exc.add_note("SSH diagnostics: " + _format_ssh_diagnostics(diagnostics))
            LOGGER.error(
                "Paramiko connection failed. SSH diagnostics: %s",
                _format_ssh_diagnostics(diagnostics),
                exc_info=True,
            )
            raise

        try:
            transport = client.get_transport()
            if not transport or not transport.is_active():
                raise RuntimeError("SSH transport failed to start.")

            if profile.keepalive_s:
                transport.set_keepalive(profile.keepalive_s)
        except Exception:
            _close_quietly(client)
            raise

        self.client = client

    def ensure_available(self) -> None:
        if self.client is None:
            raise RuntimeError("Remote client is not connected.")

        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise RuntimeError("SSH transport is not active.")

    def close(self) -> None:
        client = self.client
        self.client = None
        _close_quietly(client)

    def _open_sftp(self):
        self.ensure_available()
        sftp = self.client.open_sftp()
        _set_timeout_quietly(_sftp_channel(sftp), DEFAULT_SFTP_TIMEOUT_S)
        return sftp

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
            parts.append("module purge >/dev/null 2>&1 || true")
            parts.extend(
                f"module load {shlex.quote(module_name)} >/dev/null 2>&1 || true"
                for module_name in MODULES
            )

        parts.append(command)
        full_command = "\n".join(parts)
        started = time.time()
        effective_timeout_s = (
            DEFAULT_REMOTE_COMMAND_TIMEOUT_S
            if timeout_s is None
            else timeout_s
        )
        stdin = stdout = stderr = None
        channel = None
        try:
            stdin, stdout, stderr = self.client.exec_command(
                full_command,
                get_pty=False,
                timeout=effective_timeout_s,
            )
            _close_quietly(stdin)
            channel = getattr(stdout, "channel", None)
            out = stdout.read().decode("utf-8", "ignore")
            err = stderr.read().decode("utf-8", "ignore")
            returncode = channel.recv_exit_status() if channel is not None else 0
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
        finally:
            if channel is None and stdout is not None:
                channel = getattr(stdout, "channel", None)
            _close_quietly(stderr)
            _close_quietly(stdout)
            _close_quietly(stdin)
            _close_quietly(channel)

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

        sftp = self._open_sftp()
        try:
            with sftp.file(remote_path, "w") as handle:
                handle.write(text)
            sftp.chmod(remote_path, mode)
        finally:
            _close_quietly(sftp)

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

        sftp = self._open_sftp()
        try:
            with sftp.file(remote_path, "wb") as handle:
                handle.write(data)
            sftp.chmod(remote_path, mode)
        finally:
            _close_quietly(sftp)

        return RemoteTransferResult(
            remote_path=remote_path,
            bytes_transferred=len(data),
            mode=mode,
        )

    def read_bytes(self, remote_path: str, *, max_bytes: int | None = None) -> bytes:
        self.ensure_available()
        sftp = self._open_sftp()
        try:
            with sftp.file(remote_path, "rb") as handle:
                data = handle.read(max_bytes) if max_bytes else handle.read()
        finally:
            _close_quietly(sftp)
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

        sftp = self._open_sftp()
        try:
            sftp.put(local_path, remote_path)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            _close_quietly(sftp)

        return RemoteTransferResult(remote_path=remote_path, local_path=local_path, mode=mode)

    def download_file(self, remote_path: str, local_path: str) -> RemoteTransferResult:
        self.ensure_available()
        sftp = self._open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            _close_quietly(sftp)
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
            _print_sbatch_result(result.stdout or "", result.stderr or "", None)
            raise RemoteExecutionError(result)

        try:
            job_id = parse_sbatch_job_id(result.stdout)
        except Exception:
            _print_sbatch_result(result.stdout or "", result.stderr or "", None)
            raise

        _print_sbatch_result(result.stdout or "", result.stderr or "", job_id)
        return BatchSubmissionResult(job_id=job_id, raw_output=result.stdout, command=command)

    def submit(self, submission_spec: dict, dry_run: bool = False) -> JobRecord:
        self.ensure_available()
        self._preflight(submission_spec)

        output = self._prepare_submission_files(submission_spec)
        if dry_run:
            return self._job_record(
                submission_spec,
                None,
                output + "DRY RUN\n",
                status="dry_run",
            )

        batch_result = self.submit_batch(_batch_request_from_submission_spec(submission_spec))
        sbatch_raw = (batch_result.raw_output or "").strip() or batch_result.job_id
        output += f"Submitting with: {batch_result.command}\n"
        output += f"SBATCH_RAW_OUT={sbatch_raw}\n"

        job_id = batch_result.job_id
        record = self._job_record(submission_spec, job_id, output)
        self._write_remote_job_record(record)
        return record

    def _prepare_submission_files(self, submission_spec: dict) -> str:
        output_lines = []

        self._prepare_remote_directories(submission_spec)
        output_lines.append("PREP_OK=Remote directories prepared")

        self._prepare_remote_directory(
            submission_spec["paths"]["run_dir"],
            "Working directory created",
        )
        output_lines.append("PREP_OK=Working directory created")

        script_group = None
        for group in remote_preparation_file_groups(submission_spec):
            if group["step"] == "Submission script written":
                script_group = group
                continue

            self._upload_preparation_file_group(group)
            output_lines.append(f"PREP_OK={group['step']}")

        if submission_spec.get("potcar", {}).get("symlink_targets"):
            self._prepare_potcar_symlinks(submission_spec)
            output_lines.append("PREP_OK=POTCAR links prepared")

        if script_group is not None:
            self._upload_preparation_file_group(script_group)
            output_lines.append("PREP_OK=Submission script written")

        output_lines.append("PREP_OK=Ready for submission")
        return "\n".join(output_lines) + "\n"

    def _prepare_remote_directories(self, submission_spec: dict) -> None:
        def action() -> None:
            for path in submission_spec["paths"].get("directories_to_prepare", []):
                self.ensure_directory(path)
                if not self.is_dir(path):
                    self._raise_preparation_failure(
                        "Remote directories prepared",
                        f"Expected directory does not exist: {path}",
                        f"test -d {shlex.quote(path)}",
                    )

        self._run_preparation_step("Remote directories prepared", action)

    def _prepare_remote_directory(self, path: str, stage: str) -> None:
        def action() -> None:
            self.ensure_directory(path)
            if not self.is_dir(path):
                self._raise_preparation_failure(
                    stage,
                    f"Expected directory does not exist: {path}",
                    f"test -d {shlex.quote(path)}",
                )

        self._run_preparation_step(stage, action)

    def _upload_preparation_file_group(self, group: Mapping[str, Any]) -> None:
        stage = str(group["step"])

        def action() -> None:
            for item in group.get("files", []):
                remote_path = str(item["path"])
                self.put_text(
                    remote_path,
                    str(item.get("text", "")),
                    mode=int(item.get("mode", 0o640)),
                )
                if not self.is_file(remote_path):
                    self._raise_preparation_failure(
                        stage,
                        f"Expected file does not exist: {remote_path}",
                        f"test -f {shlex.quote(remote_path)}",
                    )

        self._run_preparation_step(stage, action)

    def _prepare_potcar_symlinks(self, submission_spec: dict) -> None:
        potcar = submission_spec.get("potcar", {})
        target = str(potcar["target"])

        def action() -> None:
            for link in potcar.get("symlink_targets", []):
                self.symlink(target, str(link), overwrite=True)
                verify_command = (
                    f"test -L {shlex.quote(str(link))} "
                    f"&& test \"$(readlink {shlex.quote(str(link))} 2>/dev/null)\" "
                    f"= {shlex.quote(target)}"
                )
                result = self.run(verify_command, check=False)
                if not result.ok:
                    self._raise_preparation_failure(
                        "POTCAR links prepared",
                        f"Expected POTCAR symlink does not point to {target}: {link}",
                        verify_command,
                    )

        self._run_preparation_step("POTCAR links prepared", action)

    def _run_preparation_step(self, stage: str, action) -> None:
        try:
            action()
        except RemoteExecutionError as exc:
            if _has_preparation_failure_marker(exc):
                raise
            reason = str(exc).strip() or f"Unable to complete remote preparation stage: {stage}"
            raise RemoteExecutionError(
                _preparation_failure_result(stage, reason, exc.result.command)
            ) from exc
        except Exception as exc:
            reason = str(exc).strip() or f"Unable to complete remote preparation stage: {stage}"
            raise RemoteExecutionError(
                _preparation_failure_result(stage, reason, stage)
            ) from exc

    def _raise_preparation_failure(self, stage: str, reason: str, command: str) -> None:
        raise RemoteExecutionError(_preparation_failure_result(stage, reason, command))

    def query_job(self, job_id: str) -> RemoteJobStatus:
        self.ensure_available()
        stripped_job_id = strip_job_id(job_id)
        if not stripped_job_id:
            raise ValueError("SLURM job ID is empty.")

        scontrol_command = f"/usr/bin/scontrol show job {shlex.quote(stripped_job_id)}"
        squeue_command = f"/usr/bin/squeue -j {shlex.quote(stripped_job_id)} -h -o %T"
        scontrol_result = self._run_monitor_command("scontrol", scontrol_command)
        squeue_result = self._run_monitor_command("squeue", squeue_command)
        initial_commands = _monitoring_command_summary(
            scontrol_command,
            squeue_command,
        )

        status = remote_job_status_from_slurm_outputs(
            stripped_job_id,
            squeue_output=squeue_result.stdout,
            scontrol_output=scontrol_result.stdout,
            squeue_stderr=squeue_result.stderr,
            scontrol_stderr=scontrol_result.stderr,
            command=initial_commands,
        )
        summary = status.raw.get("summary") or "UNKNOWN"

        sacct_output = ""
        sacct_brief_output = ""
        sacct_stderr = ""
        sacct_brief_stderr = ""
        final_commands = initial_commands
        if not (squeue_result.stdout or "").strip() or summary in {"SUCCESS", "FAILURE"}:
            sacct_command = (
                "/usr/bin/sacct -X -P -n "
                f"-j {shlex.quote(stripped_job_id)} "
                "--format JobIDRaw,State,ExitCode,JobName,StdOut,WorkDir"
            )
            sacct_brief_command = (
                "/usr/bin/sacct -X -n -P "
                f"-j {shlex.quote(stripped_job_id)} "
                "--format JobID,JobName%30,State,Elapsed,Start,End,Partition%20"
            )
            sacct_result = self._run_monitor_command("sacct", sacct_command)
            sacct_brief_result = self._run_monitor_command("sacct", sacct_brief_command)
            sacct_output = sacct_result.stdout
            sacct_brief_output = sacct_brief_result.stdout
            sacct_stderr = sacct_result.stderr
            sacct_brief_stderr = sacct_brief_result.stderr
            final_commands = _monitoring_command_summary(
                scontrol_command,
                squeue_command,
                sacct_command,
                sacct_brief_command,
            )

        return remote_job_status_from_slurm_outputs(
            stripped_job_id,
            squeue_output=squeue_result.stdout,
            scontrol_output=scontrol_result.stdout,
            sacct_output=sacct_output,
            sacct_brief_output=sacct_brief_output,
            squeue_stderr=squeue_result.stderr,
            scontrol_stderr=scontrol_result.stderr,
            sacct_stderr=sacct_stderr,
            sacct_brief_stderr=sacct_brief_stderr,
            command=final_commands,
        )

    def _run_monitor_command(self, stage: str, command: str) -> RemoteCommandResult:
        try:
            result = self.run(
                command,
                check=False,
                modules=False,
                export_env=False,
                timeout_s=DEFAULT_MONITOR_COMMAND_TIMEOUT_S,
            )
        except Exception as exc:
            stdout = ""
            stderr = ""
            if isinstance(exc, RemoteExecutionError):
                stdout = exc.result.stdout
                stderr = exc.result.stderr
            raise MonitoringStageError(
                stage,
                f"{stage} command failed before returning a result.",
                command=command,
                stdout=stdout,
                stderr=stderr,
                exception=exc,
            ) from exc

        return result

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


def _batch_request_from_submission_spec(submission_spec: dict) -> BatchSubmissionRequest:
    cluster = submission_spec["cluster"]
    resources = submission_spec["resources"]
    paths = submission_spec["paths"]
    return BatchSubmissionRequest(
        script_path=paths["remote_script"],
        partition=str(cluster["partition"]),
        account=str(cluster["account"]),
        nodes=int(resources["nodes"]),
        ntasks=int(resources["ntasks"]),
        mem_gb=int(resources["mem_gb"]),
        walltime=str(resources["walltime"]),
        parsable=True,
    )


def _preparation_failure_result(stage: str, reason: str, command: str) -> RemoteCommandResult:
    return RemoteCommandResult(
        command=command,
        returncode=42,
        stdout=(
            f"PREP_FAILED_STAGE={stage}\n"
            f"PREP_FAILED_REASON={reason}\n"
        ),
    )


def _has_preparation_failure_marker(exc: RemoteExecutionError) -> bool:
    result = exc.result
    combined = "\n".join(
        item
        for item in (
            result.stdout or "",
            result.stderr or "",
        )
        if item
    )
    return "PREP_FAILED_STAGE=" in combined


def _print_sbatch_result(stdout: str, stderr: str, job_id: str | None) -> None:
    LOGGER.debug("SBATCH stdout: %s", stdout)
    LOGGER.debug("SBATCH stderr: %s", stderr)
    LOGGER.debug("SBATCH job id: %s", job_id)


def _format_ssh_diagnostics(diagnostics: Mapping[str, Any]) -> str:
    return json.dumps(diagnostics, sort_keys=True, default=str)


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _looks_numeric(value: str) -> bool:
    return bool(re.match(r"^-?\d+(\.\d+)?$", str(value or "")))


def _monitoring_command_summary(*commands: str) -> str:
    return "\n".join(command for command in commands if command)


__all__ = [
    "JobRecord",
    "ParamikoRemoteRunner",
]
