from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import sys

import pytest

import backend.paramiko_remote as paramiko_remote
from backend.config import NOTEBOOK_DEFAULTS
from backend.paramiko_remote import (
    DEFAULT_REMOTE_COMMAND_TIMEOUT_S,
    DEFAULT_SFTP_TIMEOUT_S,
    ParamikoRemoteRunner,
)
from backend.remote import JobRecord, RemoteCommandResult, RemoteConnectionProfile
from backend.remote_preparation import prepare_remote_submission
from backend.remote_runtime import connected_remote_runner
from backend.submission import create_submission_spec


class CountingChannel:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.closed = False
        self.close_count = 0
        self.timeout = None
        self.recv_exit_status_count = 0

    def recv_exit_status(self):
        self.recv_exit_status_count += 1
        return self.returncode

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True
        self.close_count += 1


class CountingStream:
    def __init__(self, data=b"", *, channel=None, read_exception=None):
        self.data = data
        self.channel = channel
        self.read_exception = read_exception
        self.closed = False
        self.close_count = 0

    def read(self):
        if self.read_exception is not None:
            raise self.read_exception
        return self.data

    def close(self):
        self.closed = True
        self.close_count += 1


class CountingExecClient:
    def __init__(self, *, stdout_exception=None, returncode=0):
        self.channel = CountingChannel(returncode=returncode)
        self.stdin = CountingStream()
        self.stdout = CountingStream(
            b"stdout\n",
            channel=self.channel,
            read_exception=stdout_exception,
        )
        self.stderr = CountingStream(b"stderr\n", channel=self.channel)
        self.exec_calls = []
        self.closed = False

    def get_transport(self):
        class Transport:
            def is_active(self):
                return True

        return Transport()

    def exec_command(self, command, *, get_pty=False, timeout=None):
        self.exec_calls.append(
            {
                "command": command,
                "get_pty": get_pty,
                "timeout": timeout,
            }
        )
        return self.stdin, self.stdout, self.stderr

    def close(self):
        self.closed = True


class FailingSftpFile:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed_by_context = True

    def write(self, data):
        raise OSError("simulated SFTP write failure")


class CountingSftp:
    def __init__(self, *, fail_write=False):
        self.channel = CountingChannel()
        self.fail_write = fail_write
        self.closed = False
        self.close_count = 0
        self.files = {}
        self.modes = {}

    def get_channel(self):
        return self.channel

    def file(self, remote_path, mode):
        if self.fail_write:
            return FailingSftpFile()
        return SuccessfulSftpFile(self, remote_path)

    def chmod(self, remote_path, mode):
        self.modes[remote_path] = mode

    def close(self):
        self.closed = True
        self.close_count += 1


class SuccessfulSftpFile:
    def __init__(self, sftp, remote_path):
        self.sftp = sftp
        self.remote_path = remote_path
        self.parts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.sftp.files[self.remote_path] = "".join(self.parts)

    def write(self, data):
        self.parts.append(data.decode("utf-8") if isinstance(data, bytes) else data)


class CountingSftpClient:
    def __init__(self, sftp):
        self.sftp = sftp
        self.closed = False

    def get_transport(self):
        class Transport:
            def is_active(self):
                return True

        return Transport()

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.closed = True


class SftpRunner(ParamikoRemoteRunner):
    def ensure_directory(self, remote_path):
        return None

    def ensure_available(self):
        return None


def profile():
    return RemoteConnectionProfile(
        host=NOTEBOOK_DEFAULTS["remote_host"],
        username=NOTEBOOK_DEFAULTS["username"],
        port=NOTEBOOK_DEFAULTS["port"],
        ssh_config_host=NOTEBOOK_DEFAULTS["ssh_config_host"],
    )


def test_paramiko_run_closes_command_streams_and_channel_on_success():
    client = CountingExecClient()
    runner = ParamikoRemoteRunner(client=client)

    result = runner.run("hostname")

    assert result.ok
    assert client.exec_calls[0]["timeout"] == DEFAULT_REMOTE_COMMAND_TIMEOUT_S
    assert client.stdin.closed is True
    assert client.stdout.closed is True
    assert client.stderr.closed is True
    assert client.channel.closed is True
    assert client.channel.recv_exit_status_count == 1


def test_paramiko_run_closes_command_streams_and_channel_on_timeout():
    client = CountingExecClient(stdout_exception=TimeoutError("read timed out"))
    runner = ParamikoRemoteRunner(client=client)

    with pytest.raises(TimeoutError):
        runner.run("sleep 999")

    assert client.exec_calls[0]["timeout"] == DEFAULT_REMOTE_COMMAND_TIMEOUT_S
    assert client.stdin.closed is True
    assert client.stdout.closed is True
    assert client.stderr.closed is True
    assert client.channel.closed is True


def test_connected_remote_runner_closes_ssh_client_after_timeout():
    client = CountingExecClient(stdout_exception=TimeoutError("read timed out"))

    class TimeoutRunner(ParamikoRemoteRunner):
        def connect(self, connection_profile):
            self.client = client

    runner = TimeoutRunner()
    with pytest.raises(TimeoutError):
        with connected_remote_runner(
            profile=profile(),
            runner_factory=lambda: runner,
        ) as connected:
            connected.run("sleep 999")

    assert client.channel.closed is True
    assert client.closed is True
    assert runner.client is None


def test_connect_closes_authenticated_client_if_transport_setup_fails(monkeypatch):
    class FakeTransport:
        def is_active(self):
            return True

        def set_keepalive(self, keepalive_s):
            raise RuntimeError("keepalive setup failed")

    class FakeSshClient:
        instance = None

        def __init__(self):
            self.closed = False
            FakeSshClient.instance = self

        def set_missing_host_key_policy(self, policy):
            self.policy = policy

        def connect(self, **kwargs):
            self.connect_kwargs = kwargs

        def get_transport(self):
            return FakeTransport()

        def close(self):
            self.closed = True

    class FakeParamikoModule:
        SSHClient = FakeSshClient

        class AutoAddPolicy:
            pass

    monkeypatch.setitem(sys.modules, "paramiko", FakeParamikoModule)
    monkeypatch.setattr(
        paramiko_remote,
        "_paramiko_connect_details",
        lambda connection_profile, paramiko_module: ({}, {}),
    )

    runner = ParamikoRemoteRunner()
    connection_profile = RemoteConnectionProfile(
        host=NOTEBOOK_DEFAULTS["remote_host"],
        username=NOTEBOOK_DEFAULTS["username"],
        port=NOTEBOOK_DEFAULTS["port"],
        keepalive_s=15,
        ssh_config_host=NOTEBOOK_DEFAULTS["ssh_config_host"],
    )
    with pytest.raises(RuntimeError, match="keepalive setup failed"):
        runner.connect(connection_profile)

    assert FakeSshClient.instance.closed is True
    assert runner.client is None


def test_sftp_failure_closes_sftp_and_ssh_resources():
    sftp = CountingSftp(fail_write=True)
    client = CountingSftpClient(sftp)
    runner = SftpRunner(client=client)

    with pytest.raises(OSError):
        runner.put_text("/remote/file.txt", "payload")
    runner.close()

    assert sftp.channel.timeout == DEFAULT_SFTP_TIMEOUT_S
    assert sftp.closed is True
    assert client.closed is True
    assert runner.client is None


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
        label="TiO2 static",
        timestamp="20260629-120000",
        env={},
    )


class LifecyclePrepareRunner:
    live_connections = 0
    max_live_connections = 0
    created = []
    lock = Lock()

    def __init__(self, *, fail=False):
        self.fail = fail
        self.connected = False
        self.closed = False
        with LifecyclePrepareRunner.lock:
            LifecyclePrepareRunner.created.append(self)

    def connect(self, connection_profile):
        self.connected = True
        with LifecyclePrepareRunner.lock:
            LifecyclePrepareRunner.live_connections += 1
            LifecyclePrepareRunner.max_live_connections = max(
                LifecyclePrepareRunner.max_live_connections,
                LifecyclePrepareRunner.live_connections,
            )

    def submit(self, spec, dry_run=False):
        if self.fail:
            raise RuntimeError("preparation failed")
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
            submitted_at="",
            raw_output=(
                "PREP_OK=Remote directories prepared\n"
                "PREP_OK=Working directory created\n"
                "PREP_OK=submission.json uploaded\n"
                "PREP_OK=Execution module uploaded\n"
                "PREP_OK=run_job.py uploaded\n"
                "PREP_OK=Submission script written\n"
                "PREP_OK=Ready for submission\n"
            ),
            status="dry_run",
        )

    def close(self):
        with LifecyclePrepareRunner.lock:
            if self.connected and not self.closed:
                LifecyclePrepareRunner.live_connections -= 1
            self.closed = True


def reset_lifecycle_runner_counts():
    with LifecyclePrepareRunner.lock:
        LifecyclePrepareRunner.live_connections = 0
        LifecyclePrepareRunner.max_live_connections = 0
        LifecyclePrepareRunner.created = []


def test_prepare_remote_closes_connection_after_success_and_failure():
    spec = submission_spec()
    reset_lifecycle_runner_counts()

    success = prepare_remote_submission(
        spec,
        runner_factory=lambda: LifecyclePrepareRunner(),
    )
    failure = prepare_remote_submission(
        spec,
        runner_factory=lambda: LifecyclePrepareRunner(fail=True),
    )

    assert success["status"] == "success"
    assert failure["status"] == "failed"
    assert LifecyclePrepareRunner.live_connections == 0
    assert all(runner.closed for runner in LifecyclePrepareRunner.created)


def test_repeated_sequential_remote_operations_do_not_accumulate_connections():
    spec = submission_spec()
    reset_lifecycle_runner_counts()

    for _ in range(5):
        result = prepare_remote_submission(
            spec,
            runner_factory=lambda: LifecyclePrepareRunner(),
        )
        assert result["status"] == "success"
        assert LifecyclePrepareRunner.live_connections == 0

    assert len(LifecyclePrepareRunner.created) == 5
    assert all(runner.closed for runner in LifecyclePrepareRunner.created)


def test_concurrent_remote_operations_close_connections_after_completion():
    spec = submission_spec()
    reset_lifecycle_runner_counts()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: prepare_remote_submission(
                    spec,
                    runner_factory=lambda: LifecyclePrepareRunner(),
                ),
                range(8),
            )
        )

    assert all(result["status"] == "success" for result in results)
    assert LifecyclePrepareRunner.live_connections == 0
    assert len(LifecyclePrepareRunner.created) == 8
    assert all(runner.closed for runner in LifecyclePrepareRunner.created)
