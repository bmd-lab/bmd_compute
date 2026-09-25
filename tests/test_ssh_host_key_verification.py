import sys
import types

import pytest

import backend.paramiko_remote as paramiko_remote
import backend.remote_preparation as remote_preparation
import backend.remote_submission as remote_submission
from backend.paramiko_remote import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_REMOTE_COMMAND_TIMEOUT_S,
    ParamikoRemoteRunner,
    SshHostKeyTrustError,
    _configure_host_key_verification,
)
from backend.remote import RemoteConnectionProfile


class RejectPolicy:
    pass


class FakeTransport:
    def is_active(self):
        return True

    def set_keepalive(self, value):
        self.keepalive = value


class FakeSSHException(Exception):
    pass


class FakeAuthenticationException(Exception):
    pass


class BadHostKeyException(Exception):
    pass


class FakeSSHClient:
    behavior = None
    instance = None

    def __init__(self):
        self.loaded = []
        self.closed = False
        self.connect_calls = []
        FakeSSHClient.instance = self

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def load_system_host_keys(self, filename):
        self.loaded.append(filename)

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        if self.behavior:
            raise self.behavior

    def get_transport(self):
        return FakeTransport()

    def close(self):
        self.closed = True


def _paramiko_module():
    return types.SimpleNamespace(
        SSHClient=FakeSSHClient,
        RejectPolicy=RejectPolicy,
        SSHException=FakeSSHException,
        AuthenticationException=FakeAuthenticationException,
        BadHostKeyException=BadHostKeyException,
    )


def _profile():
    return RemoteConnectionProfile(
        host="cluster.example",
        username="student",
        port=22,
        keepalive_s=30,
        ssh_config_host="cluster-alias",
    )


def _connect_details(known_hosts):
    return (
        {
            "hostname": "cluster.example",
            "port": 22,
            "username": "student",
            "timeout": DEFAULT_CONNECT_TIMEOUT_S,
            "banner_timeout": DEFAULT_CONNECT_TIMEOUT_S,
            "auth_timeout": DEFAULT_CONNECT_TIMEOUT_S,
            "channel_timeout": DEFAULT_REMOTE_COMMAND_TIMEOUT_S,
        },
        {"known_hosts_candidates": [str(known_hosts)]},
    )


def test_trusted_matching_host_key_is_accepted(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("cluster.example ssh-ed25519 AAAATEST\n", encoding="utf-8")
    module = _paramiko_module()
    FakeSSHClient.behavior = None
    monkeypatch.setitem(sys.modules, "paramiko", module)
    monkeypatch.setattr(
        paramiko_remote,
        "_paramiko_connect_details",
        lambda profile, paramiko: _connect_details(known_hosts),
    )

    runner = ParamikoRemoteRunner()
    runner.connect(_profile())

    assert FakeSSHClient.instance.loaded == [str(known_hosts)]
    assert isinstance(FakeSSHClient.instance.policy, RejectPolicy)
    connect_kwargs = FakeSSHClient.instance.connect_calls[0]
    assert connect_kwargs["timeout"] == DEFAULT_CONNECT_TIMEOUT_S
    assert connect_kwargs["banner_timeout"] == DEFAULT_CONNECT_TIMEOUT_S
    assert connect_kwargs["auth_timeout"] == DEFAULT_CONNECT_TIMEOUT_S
    assert connect_kwargs["channel_timeout"] == DEFAULT_REMOTE_COMMAND_TIMEOUT_S
    runner.close()


@pytest.mark.parametrize(
    "error",
    [
        FakeSSHException("Server 'cluster.example' not found in known_hosts"),
        BadHostKeyException("Host key for server does not match"),
    ],
)
def test_unknown_or_changed_host_key_is_rejected(monkeypatch, tmp_path, error):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("cluster.example ssh-ed25519 AAAATEST\n", encoding="utf-8")
    module = _paramiko_module()
    FakeSSHClient.behavior = error
    monkeypatch.setitem(sys.modules, "paramiko", module)
    monkeypatch.setattr(
        paramiko_remote,
        "_paramiko_connect_details",
        lambda profile, paramiko: _connect_details(known_hosts),
    )

    with pytest.raises(type(error)):
        ParamikoRemoteRunner().connect(_profile())
    assert FakeSSHClient.instance.closed is True


def test_missing_or_misconfigured_trust_source_fails_before_connect(tmp_path):
    client = FakeSSHClient()
    with pytest.raises(SshHostKeyTrustError, match="No readable SSH known-hosts"):
        _configure_host_key_verification(client, _paramiko_module(), [tmp_path / "missing"])
    assert client.connect_calls == []
    assert isinstance(client.policy, RejectPolicy)


def test_malformed_known_hosts_file_has_clear_failure(tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("not a valid known-hosts entry\n", encoding="utf-8")

    class MalformedKnownHostsClient(FakeSSHClient):
        def load_system_host_keys(self, filename):
            raise ValueError("invalid host key")

    client = MalformedKnownHostsClient()
    with pytest.raises(SshHostKeyTrustError, match="Unable to load trusted SSH host keys"):
        _configure_host_key_verification(client, _paramiko_module(), [known_hosts])
    assert client.connect_calls == []


def test_authentication_failure_remains_distinguishable(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("cluster.example ssh-ed25519 AAAATEST\n", encoding="utf-8")
    error = FakeAuthenticationException("Authentication failed")
    module = _paramiko_module()
    FakeSSHClient.behavior = error
    monkeypatch.setitem(sys.modules, "paramiko", module)
    monkeypatch.setattr(
        paramiko_remote,
        "_paramiko_connect_details",
        lambda profile, paramiko: _connect_details(known_hosts),
    )

    with pytest.raises(FakeAuthenticationException):
        ParamikoRemoteRunner().connect(_profile())
    reason = remote_submission._submission_reason(error)
    assert reason == "SSH authentication failed."
    stage, prep_reason, _suggestion = remote_preparation._classify_failure(
        error,
        "Remote connection established",
        {"cluster": {"remote_host": "cluster.example", "username": "student"}},
    )
    assert stage == "SSH Authentication"
    assert prep_reason == "SSH authentication failed."


def test_host_key_failures_have_clear_user_facing_classification():
    error = SshHostKeyTrustError("No readable SSH known-hosts file was found")
    stage, reason, suggestion = remote_preparation._classify_failure(
        error,
        "Remote connection established",
        {"cluster": {"remote_host": "cluster.example", "username": "student"}},
    )
    assert stage == "SSH Host Verification"
    assert "identity verification failed" in reason
    assert "known-hosts" in suggestion
    assert remote_submission._submission_reason(error) == (
        "SSH host identity verification failed."
    )
