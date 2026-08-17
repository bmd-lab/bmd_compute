from __future__ import annotations

import logging
import os
import shlex
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

from backend.execution import (
    _flow_stage_artifacts,
    _flow_stage_directories,
    _jobflow_failure_tracebacks_to_stderr,
    _run_locally_with_stage_directories,
    configure_atomate2_vasp_command,
    resolve_vasp_cmd_argv,
)


logger = logging.getLogger("jobflow.managers.local")
stderr = StringIO()

with redirect_stderr(stderr), _jobflow_failure_tracebacks_to_stderr():
    logger.info("Started executing jobs locally")
    logger.info(
        "Static failed with exception:\n"
        "Traceback (most recent call last):\n"
        "  File \"job.py\", line 1, in run\n"
        "RuntimeError: underlying atomate2 failure\n"
    )

output = stderr.getvalue()

assert "Started executing jobs locally" not in output
assert "Static failed with exception:" in output
assert "Traceback (most recent call last):" in output
assert "RuntimeError: underlying atomate2 failure" in output


vasp_command_spec = {
    "environment": {
        "VASP_CMD": "mpirun -n $SLURM_NTASKS vasp_std",
    },
    "resources": {
        "ntasks": 24,
    },
}
assert resolve_vasp_cmd_argv(
    vasp_command_spec,
    environ={"SLURM_NTASKS": "24"},
) == ["mpirun", "-n", "24", "vasp_std"]
assert resolve_vasp_cmd_argv(
    {
        **vasp_command_spec,
        "resources": {"ntasks": 36},
    },
    environ={"SLURM_NTASKS": "36"},
) == ["mpirun", "-n", "36", "vasp_std"]
assert resolve_vasp_cmd_argv(
    vasp_command_spec,
    environ={},
) == ["mpirun", "-n", "24", "vasp_std"]

saved_atomate2 = sys.modules.get("atomate2")
saved_vasp_cmd = os.environ.get("VASP_CMD")
fake_atomate2 = ModuleType("atomate2")
fake_atomate2.SETTINGS = SimpleNamespace(VASP_CMD="vasp_std")
sys.modules["atomate2"] = fake_atomate2
try:
    configured = configure_atomate2_vasp_command(
        vasp_command_spec,
        environ={"SLURM_NTASKS": "24"},
    )
    assert configured == ["mpirun", "-n", "24", "vasp_std"]
    assert fake_atomate2.SETTINGS.VASP_CMD == "mpirun -n 24 vasp_std"
    assert shlex.split(fake_atomate2.SETTINGS.VASP_CMD) == [
        "mpirun",
        "-n",
        "24",
        "vasp_std",
    ]

    configured = configure_atomate2_vasp_command(
        {
            **vasp_command_spec,
            "resources": {"ntasks": 48},
        },
        environ={"SLURM_NTASKS": "48"},
    )
    assert configured == ["mpirun", "-n", "48", "vasp_std"]
    assert fake_atomate2.SETTINGS.VASP_CMD == "mpirun -n 48 vasp_std"
    assert shlex.split(fake_atomate2.SETTINGS.VASP_CMD) == [
        "mpirun",
        "-n",
        "48",
        "vasp_std",
    ]
finally:
    if saved_vasp_cmd is None:
        os.environ.pop("VASP_CMD", None)
    else:
        os.environ["VASP_CMD"] = saved_vasp_cmd
    if saved_atomate2 is None:
        sys.modules.pop("atomate2", None)
    else:
        sys.modules["atomate2"] = saved_atomate2


class FakeStore:
    def __init__(self):
        self.connected = False

    def connect(self):
        self.connected = True


class FakeResponse:
    def __init__(self):
        self.stored_data = None
        self.stop_children = False
        self.stop_jobflow = False
        self.replace = None
        self.detour = None
        self.addition = None
        self.job_dir = None


class FakeJob:
    def __init__(self, name, uuid):
        self.name = name
        self.uuid = uuid
        self.index = 1
        self.config = SimpleNamespace(on_missing_references="ERROR")
        self.cwd = None

    def run(self, *, store):
        self.cwd = Path.cwd()
        (self.cwd / "OUTCAR").write_text(f"output for {self.name}", encoding="utf-8")
        return FakeResponse()


class FakeFlow:
    def __init__(self):
        self.jobs = [FakeJob("relax_01", "job-1"), FakeJob("relax_02", "job-2")]
        self.metadata = {"bmd_stage_directories": ("relax_01", "relax_02")}

    def iterflow(self):
        yield self.jobs[0], []
        yield self.jobs[1], [self.jobs[0].uuid]


def install_fake_jobflow_modules():
    module_names = (
        "jobflow",
        "jobflow.core",
        "jobflow.core.flow",
        "jobflow.core.reference",
    )
    saved_modules = {name: sys.modules.get(name) for name in module_names}

    for name in module_names:
        sys.modules[name] = ModuleType(name)

    store = FakeStore()
    sys.modules["jobflow"].SETTINGS = SimpleNamespace(JOB_STORE=store)
    sys.modules["jobflow"].initialize_logger = lambda fmt="": None
    sys.modules["jobflow.core.flow"].get_flow = (
        lambda flow, allow_external_references=False: flow
    )
    sys.modules["jobflow.core.reference"].OnMissing = SimpleNamespace(ERROR="ERROR")

    return saved_modules, store


def restore_modules(saved_modules):
    for name, module in saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


flow = FakeFlow()
assert _flow_stage_directories(flow) == ("relax_01", "relax_02")
assert _flow_stage_artifacts(flow) == ()

saved_modules, store = install_fake_jobflow_modules()
try:
    with tempfile.TemporaryDirectory(prefix="bmd-staged-run-") as tmpdir:
        responses = _run_locally_with_stage_directories(
            flow,
            ("relax_01", "relax_02"),
            root_dir=tmpdir,
            ensure_success=True,
        )

        root = Path(tmpdir).resolve()
        assert store.connected is True
        assert (
            (root / "relax_01" / "OUTCAR").read_text(encoding="utf-8")
            == "output for relax_01"
        )
        assert (
            (root / "relax_02" / "OUTCAR").read_text(encoding="utf-8")
            == "output for relax_02"
        )
        assert not (root / "OUTCAR").exists()
        assert flow.jobs[0].cwd == root / "relax_01"
        assert flow.jobs[1].cwd == root / "relax_02"
        assert responses["job-1"][1].job_dir == root / "relax_01"
        assert responses["job-2"][1].job_dir == root / "relax_02"
finally:
    restore_modules(saved_modules)


class FakeArtifactJob(FakeJob):
    def __init__(self, name, uuid, *, write_wavecar=False, require_wavecar=False):
        super().__init__(name, uuid)
        self.write_wavecar = write_wavecar
        self.require_wavecar = require_wavecar

    def run(self, *, store):
        self.cwd = Path.cwd()
        if self.require_wavecar:
            assert (self.cwd / "WAVECAR").read_text(encoding="utf-8") == "wavecar"
        if self.write_wavecar:
            (self.cwd / "WAVECAR").write_text("wavecar", encoding="utf-8")
        (self.cwd / "OUTCAR").write_text(f"output for {self.name}", encoding="utf-8")
        return FakeResponse()


class FakeArtifactFlow:
    def __init__(self, *, write_wavecar=True, require_wavecar=True):
        self.jobs = [
            FakeArtifactJob("stage_01", "artifact-job-1", write_wavecar=write_wavecar),
            FakeArtifactJob("stage_02", "artifact-job-2", require_wavecar=require_wavecar),
        ]
        self.metadata = {
            "bmd_stage_directories": ("stage_01", "stage_02"),
            "bmd_stage_artifacts": [
                {"write_wavecar": True, "copy_from_previous": []},
                {"write_wavecar": False, "copy_from_previous": ["WAVECAR"]},
            ],
        }

    def iterflow(self):
        yield self.jobs[0], []
        yield self.jobs[1], [self.jobs[0].uuid]


artifact_flow = FakeArtifactFlow()
assert _flow_stage_artifacts(artifact_flow) == (
    {"write_wavecar": True, "copy_from_previous": []},
    {"write_wavecar": False, "copy_from_previous": ["WAVECAR"]},
)
saved_modules, store = install_fake_jobflow_modules()
try:
    with tempfile.TemporaryDirectory(prefix="bmd-wavecar-run-") as tmpdir:
        _run_locally_with_stage_directories(
            artifact_flow,
            ("stage_01", "stage_02"),
            root_dir=tmpdir,
            ensure_success=True,
        )

        root = Path(tmpdir).resolve()
        assert (root / "stage_01" / "WAVECAR").read_text(encoding="utf-8") == "wavecar"
        assert (root / "stage_02" / "WAVECAR").read_text(encoding="utf-8") == "wavecar"
finally:
    restore_modules(saved_modules)

missing_wavecar_flow = FakeArtifactFlow(write_wavecar=False, require_wavecar=False)
saved_modules, store = install_fake_jobflow_modules()
try:
    with tempfile.TemporaryDirectory(prefix="bmd-missing-wavecar-run-") as tmpdir:
        _run_locally_with_stage_directories(
            missing_wavecar_flow,
            ("stage_01", "stage_02"),
            root_dir=tmpdir,
            ensure_success=True,
        )

        root = Path(tmpdir).resolve()
        assert not (root / "stage_01" / "WAVECAR").exists()
        assert not (root / "stage_02" / "WAVECAR").exists()
finally:
    restore_modules(saved_modules)

print("execution diagnostics smoke test passed")
