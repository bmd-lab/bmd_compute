from __future__ import annotations

import json

from backend.config import DEFAULT_LOGS_DIR, DEFAULT_REMOTE_HOST, DEFAULT_USERNAME
from backend.results import (
    load_results_for_completed_job,
    monitoring_indicates_success,
    remote_job_state_path,
)


monitoring_success = {
    "status": "success",
    "job_id": "123456",
    "slurm_state": "COMPLETED",
    "summary": "SUCCESS",
    "exit_code": "0:0",
    "workdir": "/home/leeburton",
}

RUN_DIR = "/bmd-db/lee/flows/TiO2-static-20260629-120000"

submission_spec = {
    "cluster": {
        "remote_host": DEFAULT_REMOTE_HOST,
        "username": DEFAULT_USERNAME,
        "port": 22,
    },
    "paths": {
        "run_dir": RUN_DIR,
    },
}


class ResultsRunner:
    def __init__(self, *, missing: str | None = None):
        self.missing = missing
        self.connected_profile = None
        self.closed = False
        self.checked_paths = []
        self.read_paths = []
        self.state_path = None

    def connect(self, profile):
        self.connected_profile = profile

    def close(self):
        self.closed = True

    def run(self, *args, **kwargs):
        raise AssertionError("Results should not use recursive shell discovery")

    def is_file(self, remote_path):
        self.checked_paths.append(remote_path)
        if remote_path == remote_job_state_path("123456"):
            return self.missing != "state"
        if remote_path.endswith("/vasprun.xml"):
            return self.missing != "vasprun"
        if remote_path.endswith("/CONTCAR"):
            return self.missing != "contcar"
        if remote_path.endswith("/OUTCAR"):
            return self.missing != "outcar"
        return False

    def read_text(self, remote_path, *, max_bytes=None):
        self.state_path = remote_path
        return json.dumps({"run_dir": RUN_DIR})

    def read_bytes(self, remote_path, *, max_bytes=None):
        self.read_paths.append(remote_path)
        return f"contents for {remote_path}\n".encode()


def fake_parser(files, monitoring_result):
    assert monitoring_result["job_id"] == "123456"
    assert set(files) == {"contcar", "outcar", "vasprun"}
    return {
        "status": "success",
        "completion_status": "COMPLETED (ExitCode 0:0)",
        "final_energy_ev": -10.25,
        "energy_per_atom_ev": -5.125,
        "ionic_steps": 7,
        "electronic_convergence": True,
        "final_formula": "TiO2",
        "natoms": 3,
        "diagnostics": {"parser": "fake", "outcar_parsed": True, "outcar_error": ""},
        "viewer": {"format": "cif", "source": "CONTCAR", "cif": "data_TiO2\n"},
    }


def test_monitoring_success_detection_requires_completed_success():
    assert monitoring_indicates_success(monitoring_success)
    assert not monitoring_indicates_success({**monitoring_success, "summary": "RUNNING"})
    assert not monitoring_indicates_success({**monitoring_success, "exit_code": "1:0"})
    assert not monitoring_indicates_success({**monitoring_success, "slurm_state": "FAILED"})


def test_results_load_for_submitted_completed_job_uses_submission_profile():
    runner = ResultsRunner()
    result = load_results_for_completed_job(
        monitoring_success,
        submission_spec=submission_spec,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    assert runner.connected_profile.host == DEFAULT_REMOTE_HOST
    assert runner.connected_profile.username == DEFAULT_USERNAME
    assert runner.closed is True
    assert runner.checked_paths == [
        f"{RUN_DIR}/CONTCAR",
        f"{RUN_DIR}/OUTCAR",
        f"{RUN_DIR}/vasprun.xml",
    ]
    assert runner.state_path is None
    assert len(runner.read_paths) == 3
    assert result["status"] == "success"
    assert result["final_formula"] == "TiO2"
    assert result["files"]["contcar"] == f"{RUN_DIR}/CONTCAR"
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == RUN_DIR


def test_results_load_for_resumed_completed_job_uses_default_profile():
    runner = ResultsRunner()
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    assert runner.connected_profile.host == DEFAULT_REMOTE_HOST
    assert runner.connected_profile.username == DEFAULT_USERNAME
    assert runner.closed is True
    assert runner.state_path == f"{DEFAULT_LOGS_DIR}/job_123456.json"
    assert runner.checked_paths == [
        f"{DEFAULT_LOGS_DIR}/job_123456.json",
        f"{RUN_DIR}/CONTCAR",
        f"{RUN_DIR}/OUTCAR",
        f"{RUN_DIR}/vasprun.xml",
    ]
    assert result["status"] == "success"
    assert result["job_id"] == "123456"
    assert result["run_dir"] == RUN_DIR


def test_results_are_skipped_until_monitoring_reports_success():
    class UnusedRunner(ResultsRunner):
        def connect(self, profile):
            raise AssertionError("Results should not connect before successful completion")

    result = load_results_for_completed_job(
        {**monitoring_success, "summary": "RUNNING", "slurm_state": "RUNNING"},
        runner_factory=UnusedRunner,
        parser=fake_parser,
    )

    assert result is None


def test_results_report_missing_required_output_file():
    runner = ResultsRunner(missing="vasprun")
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "Results Discovery"
    assert "vasprun.xml" in result["reason"]
    assert runner.closed is True


def test_results_report_missing_bmd_job_state_for_resume():
    runner = ResultsRunner(missing="state")
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    assert result["status"] == "failed"
    assert result["stage"] == "Results Discovery"
    assert "canonical BMD run directory" in result["reason"]
    assert runner.checked_paths == [f"{DEFAULT_LOGS_DIR}/job_123456.json"]
    assert runner.closed is True


if __name__ == "__main__":
    test_monitoring_success_detection_requires_completed_success()
    test_results_load_for_submitted_completed_job_uses_submission_profile()
    test_results_load_for_resumed_completed_job_uses_default_profile()
    test_results_are_skipped_until_monitoring_reports_success()
    test_results_report_missing_required_output_file()
    test_results_report_missing_bmd_job_state_for_resume()
    print("results smoke test passed")
