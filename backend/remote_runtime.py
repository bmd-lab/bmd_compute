from __future__ import annotations

import logging
import math
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from backend.config import (
    MAX_CONCURRENT_REMOTE_OPERATIONS,
    NOTEBOOK_DEFAULTS,
    REMOTE_OPERATION_SLOT_TIMEOUT_S,
)
from backend.remote import RemoteConnectionProfile, RemoteOperationBusy, RemoteRunner


LOGGER = logging.getLogger(__name__)


class RemoteOperationLimiter:
    """
    Process-local concurrency gate for SSH-backed remote operations.

    This deliberately coordinates only within the current Uvicorn/Python
    process. If deployment later uses multiple workers, the effective limit is
    approximately workers * limit unless a cross-process coordinator is added.
    """

    def __init__(self, *, limit: int, acquire_timeout_s: float):
        if int(limit) <= 0:
            raise ValueError("Remote operation limit must be a positive integer.")
        if not math.isfinite(float(acquire_timeout_s)) or float(acquire_timeout_s) <= 0:
            raise ValueError("Remote operation acquire timeout must be positive.")
        self.limit = int(limit)
        self.acquire_timeout_s = float(acquire_timeout_s)
        self._semaphore = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self._active = 0
        self._max_active_observed = 0
        self._busy_rejections = 0

    @contextmanager
    def slot(self, *, operation: str = "remote") -> Iterator[dict]:
        acquired = self._semaphore.acquire(timeout=self.acquire_timeout_s)
        if not acquired:
            snapshot = self.snapshot()
            with self._lock:
                self._busy_rejections += 1
                snapshot = self._snapshot_locked()
            LOGGER.warning(
                "remote operation rejected/busy",
                extra={
                    "remote_operation": operation,
                    "remote_limit": self.limit,
                    "remote_active": snapshot["active"],
                    "remote_timeout_s": self.acquire_timeout_s,
                },
            )
            raise RemoteOperationBusy(
                limit=self.limit,
                active=snapshot["active"],
                timeout_s=self.acquire_timeout_s,
            )

        with self._lock:
            self._active += 1
            self._max_active_observed = max(
                self._max_active_observed,
                self._active,
            )
            snapshot = self._snapshot_locked()
        LOGGER.debug(
            "remote slot acquired",
            extra={
                "remote_operation": operation,
                "remote_limit": self.limit,
                "remote_active": snapshot["active"],
            },
        )

        try:
            yield snapshot
        finally:
            with self._lock:
                self._active -= 1
                snapshot = self._snapshot_locked()
            self._semaphore.release()
            LOGGER.debug(
                "remote slot released",
                extra={
                    "remote_operation": operation,
                    "remote_limit": self.limit,
                    "remote_active": snapshot["active"],
                },
            )

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        return {
            "limit": self.limit,
            "active": self._active,
            "max_active_observed": self._max_active_observed,
            "busy_rejections": self._busy_rejections,
            "acquire_timeout_s": self.acquire_timeout_s,
        }


_REMOTE_OPERATION_LIMITER = RemoteOperationLimiter(
    limit=MAX_CONCURRENT_REMOTE_OPERATIONS,
    acquire_timeout_s=REMOTE_OPERATION_SLOT_TIMEOUT_S,
)


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
    return connection_profile_from_cluster(NOTEBOOK_DEFAULTS)


def connection_profile_from_cluster(cluster: dict) -> RemoteConnectionProfile:
    ssh_config_host = cluster.get("ssh_config_host") or NOTEBOOK_DEFAULTS["ssh_config_host"]
    host = cluster.get("remote_host") or ssh_config_host

    return RemoteConnectionProfile(
        host=host,
        username=cluster.get("username"),
        port=int(cluster.get("port", NOTEBOOK_DEFAULTS["port"])),
        keepalive_s=cluster.get("keepalive_s", NOTEBOOK_DEFAULTS["keepalive_s"]),
        key_file=cluster.get("key_file"),
        ssh_config_host=ssh_config_host,
    )


@contextmanager
def connected_remote_runner(
    *,
    profile: RemoteConnectionProfile,
    runner_factory: Callable[[], RemoteRunner] | None = None,
) -> Iterator[RemoteRunner]:
    with _REMOTE_OPERATION_LIMITER.slot(operation="ssh"):
        runner = create_remote_runner(runner_factory)
        try:
            runner.connect(profile)
            yield runner
        finally:
            try:
                runner.close()
            except Exception:
                pass


def remote_operation_limiter_snapshot() -> dict:
    return _REMOTE_OPERATION_LIMITER.snapshot()


__all__ = [
    "connected_remote_runner",
    "connection_profile_from_cluster",
    "connection_profile_from_submission_spec",
    "create_remote_runner",
    "default_connection_profile",
    "remote_operation_limiter_snapshot",
    "RemoteOperationLimiter",
]
