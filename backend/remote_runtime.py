from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator

from backend.config import NOTEBOOK_DEFAULTS
from backend.remote import RemoteConnectionProfile, RemoteRunner


def create_remote_runner(
    runner_factory: Callable[[], RemoteRunner] | None = None,
) -> RemoteRunner:
    if runner_factory is not None:
        return runner_factory()

    from backend.paramiko_remote import ParamikoRemoteRunner

    return ParamikoRemoteRunner()


def connection_profile_from_submission_spec(submission_spec: dict) -> RemoteConnectionProfile:
    return connection_profile_from_cluster(submission_spec["cluster"])


def default_connection_profile() -> RemoteConnectionProfile:
    return connection_profile_from_cluster({
        "remote_host": NOTEBOOK_DEFAULTS["remote_host"],
        "username": NOTEBOOK_DEFAULTS["username"],
        "port": NOTEBOOK_DEFAULTS["port"],
    })


def connection_profile_from_cluster(cluster: dict) -> RemoteConnectionProfile:
    return RemoteConnectionProfile(
        host=cluster["remote_host"],
        username=cluster["username"],
        port=int(cluster.get("port", NOTEBOOK_DEFAULTS["port"])),
        keepalive_s=NOTEBOOK_DEFAULTS["keepalive_s"],
        key_file=cluster.get("key_file"),
    )


@contextmanager
def connected_remote_runner(
    *,
    profile: RemoteConnectionProfile,
    runner_factory: Callable[[], RemoteRunner] | None = None,
) -> Iterator[RemoteRunner]:
    runner = create_remote_runner(runner_factory)
    try:
        runner.connect(profile)
        yield runner
    finally:
        try:
            runner.close()
        except Exception:
            pass


__all__ = [
    "connected_remote_runner",
    "connection_profile_from_cluster",
    "connection_profile_from_submission_spec",
    "create_remote_runner",
    "default_connection_profile",
]
