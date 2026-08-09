from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager

from backend.calculations.models import CalculationSpec, Purpose, Theory
from backend.config import (
    DEFAULT_FLOWS_DIR,
    DEFAULT_LOGS_DIR,
    DEFAULT_REMOTE_HOST,
    DEFAULT_USERNAME,
)
from backend.results import (
    load_results_for_completed_job,
    monitoring_indicates_success,
    parse_vasp_result_files,
    remote_job_state_path,
)


monitoring_success = {
    "status": "success",
    "job_id": "123456",
    "slurm_state": "COMPLETED",
    "summary": "SUCCESS",
    "exit_code": "0:0",
    "workdir": "/home/bmdguest",
}

RUN_DIR = f"{DEFAULT_FLOWS_DIR}/TiO2-static-20260629-120000"

submission_spec = {
    "cluster": {
        "ssh_config_host": DEFAULT_REMOTE_HOST,
        "remote_host": DEFAULT_REMOTE_HOST,
        "username": DEFAULT_USERNAME,
        "port": 22,
    },
    "paths": {
        "run_dir": RUN_DIR,
    },
}


class ResultsRunner:
    def __init__(self, *, missing: str | None = None, state_payload: dict | None = None):
        self.missing = missing
        self.state_payload = state_payload or {"run_dir": RUN_DIR}
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
        if remote_path.endswith("/DOSCAR"):
            return self.missing != "doscar"
        if remote_path.endswith("/KPOINTS"):
            return self.missing != "kpoints"
        if remote_path.endswith("/CONTCAR"):
            return self.missing != "contcar"
        if remote_path.endswith("/OUTCAR"):
            return self.missing != "outcar"
        return False

    def read_text(self, remote_path, *, max_bytes=None):
        self.state_path = remote_path
        return json.dumps(self.state_payload)

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


def fake_dos_parser(files, monitoring_result):
    assert monitoring_result["job_id"] == "123456"
    assert set(files) == {"contcar", "outcar", "vasprun", "doscar"}
    result = fake_parser(
        {
            "contcar": files["contcar"],
            "outcar": files["outcar"],
            "vasprun": files["vasprun"],
        },
        monitoring_result,
    )
    result["dos"] = {
        "available": True,
        "source": "vasprun.xml",
        "doscar": files["doscar"]["path"],
        "energy_points": 4001,
        "energy_min_ev": -10.0,
        "energy_max_ev": 10.0,
        "spin_channels": 1,
    }
    return result


def fake_band_parser(files, monitoring_result):
    assert monitoring_result["job_id"] == "123456"
    assert set(files) == {"contcar", "outcar", "vasprun", "kpoints"}
    result = fake_parser(
        {
            "contcar": files["contcar"],
            "outcar": files["outcar"],
            "vasprun": files["vasprun"],
        },
        monitoring_result,
    )
    result["band_structure"] = {
        "available": True,
        "source": "vasprun.xml",
        "kpoints": files["kpoints"]["path"],
        "bands": 12,
        "kpoints_count": 80,
        "spin_channels": 1,
    }
    result["visualizations"] = [
        {
            "id": "band_structure",
            "kind": "line_plot",
        },
    ]
    return result


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


def test_results_for_double_relax_use_final_stage_directory():
    double_relax_spec = {
        **submission_spec,
        "flow_spec": {
            "calculation_spec": {
                "purpose": "double_relax",
                "theory": "pbe",
                "modifiers": [],
            },
            "workflow": "double_relax",
            "potcar_functional": "PBE_64",
        },
    }
    runner = ResultsRunner()
    result = load_results_for_completed_job(
        monitoring_success,
        submission_spec=double_relax_spec,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    final_stage_dir = f"{RUN_DIR}/relax_02"
    assert runner.checked_paths == [
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["contcar"] == f"{final_stage_dir}/CONTCAR"


def test_results_for_dos_use_final_stage_directory_and_doscar():
    dos_spec = {
        **submission_spec,
        "flow_spec": {
            "calculation_spec": {
                "purpose": "dos",
                "theory": "pbe",
                "modifiers": [],
            },
            "workflow": "dos",
            "potcar_functional": "PBE_64",
        },
    }
    runner = ResultsRunner()
    result = load_results_for_completed_job(
        monitoring_success,
        submission_spec=dos_spec,
        runner_factory=lambda: runner,
        parser=fake_dos_parser,
    )

    final_stage_dir = f"{RUN_DIR}/stage_03"
    assert runner.checked_paths == [
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
        f"{final_stage_dir}/DOSCAR",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["doscar"] == f"{final_stage_dir}/DOSCAR"
    assert result["dos"]["available"] is True
    assert result["dos"]["energy_points"] == 4001


def test_results_for_band_structure_use_final_stage_directory_and_kpoints():
    band_spec = {
        **submission_spec,
        "flow_spec": {
            "calculation_spec": {
                "purpose": "band_structure",
                "theory": "pbe",
                "modifiers": [],
            },
            "workflow": "band_structure",
            "potcar_functional": "PBE_64",
        },
    }
    runner = ResultsRunner()
    result = load_results_for_completed_job(
        monitoring_success,
        submission_spec=band_spec,
        runner_factory=lambda: runner,
        parser=fake_band_parser,
    )

    final_stage_dir = f"{RUN_DIR}/stage_03"
    assert runner.checked_paths == [
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
        f"{final_stage_dir}/KPOINTS",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["kpoints"] == f"{final_stage_dir}/KPOINTS"
    assert result["band_structure"]["available"] is True
    assert result["band_structure"]["kpoints_count"] == 80
    assert result["visualizations"][0]["id"] == "band_structure"


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


def test_resumed_double_relax_uses_final_stage_directory_from_job_state():
    double_relax_state = {
        "run_dir": RUN_DIR,
        "submission_spec": {
            **submission_spec,
            "flow_spec": {
                "calculation_spec": {
                    "purpose": "double_relax",
                    "theory": "pbe",
                    "modifiers": [],
                },
                "workflow": "double_relax",
                "potcar_functional": "PBE_64",
            },
        },
    }
    runner = ResultsRunner(state_payload=double_relax_state)
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_parser,
    )

    final_stage_dir = f"{RUN_DIR}/relax_02"
    assert runner.checked_paths == [
        f"{DEFAULT_LOGS_DIR}/job_123456.json",
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["vasprun"] == f"{final_stage_dir}/vasprun.xml"


def test_resumed_dos_uses_final_stage_directory_from_job_state():
    dos_state = {
        "run_dir": RUN_DIR,
        "submission_spec": {
            **submission_spec,
            "flow_spec": {
                "calculation_spec": {
                    "purpose": "dos",
                    "theory": "pbe",
                    "modifiers": [],
                },
                "workflow": "dos",
                "potcar_functional": "PBE_64",
            },
        },
    }
    runner = ResultsRunner(state_payload=dos_state)
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_dos_parser,
    )

    final_stage_dir = f"{RUN_DIR}/stage_03"
    assert runner.checked_paths == [
        f"{DEFAULT_LOGS_DIR}/job_123456.json",
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
        f"{final_stage_dir}/DOSCAR",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["doscar"] == f"{final_stage_dir}/DOSCAR"
    assert result["dos"]["doscar"] == f"{final_stage_dir}/DOSCAR"


def test_resumed_band_structure_uses_final_stage_directory_from_job_state():
    band_state = {
        "run_dir": RUN_DIR,
        "submission_spec": {
            **submission_spec,
            "flow_spec": {
                "calculation_spec": {
                    "purpose": "band_structure",
                    "theory": "pbe",
                    "modifiers": [],
                },
                "workflow": "band_structure",
                "potcar_functional": "PBE_64",
            },
        },
    }
    runner = ResultsRunner(state_payload=band_state)
    result = load_results_for_completed_job(
        monitoring_success,
        runner_factory=lambda: runner,
        parser=fake_band_parser,
    )

    final_stage_dir = f"{RUN_DIR}/stage_03"
    assert runner.checked_paths == [
        f"{DEFAULT_LOGS_DIR}/job_123456.json",
        f"{final_stage_dir}/CONTCAR",
        f"{final_stage_dir}/OUTCAR",
        f"{final_stage_dir}/vasprun.xml",
        f"{final_stage_dir}/KPOINTS",
    ]
    assert result["run_dir"] == RUN_DIR
    assert result["workdir"] == final_stage_dir
    assert result["files"]["kpoints"] == f"{final_stage_dir}/KPOINTS"
    assert result["band_structure"]["kpoints"] == f"{final_stage_dir}/KPOINTS"


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


class FakeComposition:
    reduced_formula = "Si"


class FakeStructure:
    composition = FakeComposition()

    def __len__(self):
        return 2

    def to(self, fmt):
        assert fmt == "cif"
        return "data_Si\n"


class FakeSpin:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __str__(self):
        return self.name


class FakeTotalDos:
    energies = [-1.0, 0.0, 1.0]
    efermi = 0.0
    densities = {
        FakeSpin("up", 1): [0.0, 1.0, 0.0],
    }


class FakeKpoint:
    def __init__(self, label=None):
        self.label = label


class FakeBandStructure:
    efermi = 0.0
    distance = [0.0, 1.0, 2.0]
    kpoints = [
        FakeKpoint("\\Gamma"),
        FakeKpoint(),
        FakeKpoint("X"),
    ]
    bands = {
        FakeSpin("up", 1): [
            [-1.0, 0.0, 1.0],
            [1.0, 2.0, 3.0],
        ],
    }

    def get_band_gap(self):
        return {
            "energy": 1.0,
            "direct": True,
        }

    def is_metal(self):
        return False


class FakeVasprun:
    calls = []
    band_calls = []

    def __init__(self, path, **kwargs):
        self.calls.append({"path": path, "kwargs": kwargs})
        self.final_structure = FakeStructure()
        self.final_energy = -4.0
        self.ionic_steps = [1]
        self.converged_electronic = True
        self.tdos = FakeTotalDos()
        self.efermi = None if kwargs.get("parse_dos") is False else 0.0

    def get_band_structure(self, **kwargs):
        self.band_calls.append(kwargs)
        if self.efermi is None:
            raise ValueError("e_fermi is None.")
        return FakeBandStructure()


class FakeOutcar:
    def __init__(self, path):
        self.path = path


@contextmanager
def fake_pymatgen_results_modules():
    modules = {
        "pymatgen": types.ModuleType("pymatgen"),
        "pymatgen.core": types.ModuleType("pymatgen.core"),
        "pymatgen.io": types.ModuleType("pymatgen.io"),
        "pymatgen.io.vasp": types.ModuleType("pymatgen.io.vasp"),
        "pymatgen.io.vasp.outputs": types.ModuleType("pymatgen.io.vasp.outputs"),
    }
    modules["pymatgen.core"].Structure = types.SimpleNamespace(
        from_file=lambda path: FakeStructure(),
    )
    modules["pymatgen.io.vasp.outputs"].Outcar = FakeOutcar
    modules["pymatgen.io.vasp.outputs"].Vasprun = FakeVasprun

    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_parse_vasp_result_files_uses_vasprun_dos_for_dos_workflow():
    FakeVasprun.calls = []
    files = {
        "contcar": {"path": "/remote/stage_03/CONTCAR", "text": "contcar"},
        "outcar": {"path": "/remote/stage_03/OUTCAR", "text": "outcar"},
        "vasprun": {"path": "/remote/stage_03/vasprun.xml", "text": "<modeling />"},
        "doscar": {"path": "/remote/stage_03/DOSCAR", "text": "doscar"},
    }
    context = {
        **monitoring_success,
        "calculation_spec": CalculationSpec(Purpose.DOS, Theory.PBE).to_dict(),
    }

    with fake_pymatgen_results_modules():
        result = parse_vasp_result_files(files, context)

    assert FakeVasprun.calls
    assert FakeVasprun.calls[-1]["kwargs"]["parse_dos"] is True
    assert result["final_energy_ev"] == -4.0
    assert result["energy_per_atom_ev"] == -2.0
    assert result["viewer"]["cif"] == "data_Si\n"
    assert result["dos"]["available"] is True
    assert result["dos"]["source"] == "vasprun.xml"
    assert result["dos"]["doscar"] == "/remote/stage_03/DOSCAR"
    assert result["visualizations"][0]["id"] == "dos"
    assert result["visualizations"][0]["download_filename"] == "density_of_states.png"
    assert result["visualizations"][0]["plot"]["x"] == [-1.0, 0.0, 1.0]


def test_parse_vasp_result_files_uses_vasprun_band_structure_for_band_workflow():
    FakeVasprun.calls = []
    FakeVasprun.band_calls = []
    files = {
        "contcar": {"path": "/remote/stage_03/CONTCAR", "text": "contcar"},
        "outcar": {"path": "/remote/stage_03/OUTCAR", "text": "outcar"},
        "vasprun": {"path": "/remote/stage_03/vasprun.xml", "text": "<modeling />"},
        "kpoints": {"path": "/remote/stage_03/KPOINTS", "text": "line-mode"},
    }
    context = {
        **monitoring_success,
        "calculation_spec": CalculationSpec(Purpose.BAND_STRUCTURE, Theory.PBE).to_dict(),
    }

    with fake_pymatgen_results_modules():
        result = parse_vasp_result_files(files, context)

    assert FakeVasprun.calls
    vasprun_kwargs = FakeVasprun.calls[-1]["kwargs"]
    assert "parse_dos" not in vasprun_kwargs
    assert "parse_eigenvalues" not in vasprun_kwargs
    assert vasprun_kwargs["exception_on_bad_xml"] is False
    assert vasprun_kwargs["parse_potcar_file"] is False
    assert FakeVasprun.band_calls
    assert FakeVasprun.band_calls[-1]["line_mode"] is True
    assert FakeVasprun.band_calls[-1]["kpoints_filename"].endswith("KPOINTS")
    assert "efermi" not in FakeVasprun.band_calls[-1]
    assert result["final_energy_ev"] == -4.0
    assert result["energy_per_atom_ev"] == -2.0
    assert result["viewer"]["cif"] == "data_Si\n"
    assert result["band_structure"]["available"] is True
    assert result["band_structure"]["source"] == "vasprun.xml"
    assert result["band_structure"]["kpoints"] == "/remote/stage_03/KPOINTS"
    assert result["band_structure"]["band_gap_ev"] == 1.0
    assert result["visualizations"][0]["id"] == "band_structure"
    assert result["visualizations"][0]["download_filename"] == "band_structure.png"
    assert result["visualizations"][0]["plot"]["x"] == [0.0, 1.0, 2.0]
    assert result["visualizations"][0]["plot"]["ticktext"] == ["Γ", "X"]
    assert result["visualizations"][0]["plot"]["traces"][0]["name"] == "Bands"
    assert result["visualizations"][0]["plot"]["traces"][0]["showlegend"] is False


if __name__ == "__main__":
    test_monitoring_success_detection_requires_completed_success()
    test_results_load_for_submitted_completed_job_uses_submission_profile()
    test_results_for_double_relax_use_final_stage_directory()
    test_results_for_dos_use_final_stage_directory_and_doscar()
    test_results_for_band_structure_use_final_stage_directory_and_kpoints()
    test_results_load_for_resumed_completed_job_uses_default_profile()
    test_resumed_double_relax_uses_final_stage_directory_from_job_state()
    test_resumed_dos_uses_final_stage_directory_from_job_state()
    test_resumed_band_structure_uses_final_stage_directory_from_job_state()
    test_results_are_skipped_until_monitoring_reports_success()
    test_results_report_missing_required_output_file()
    test_results_report_missing_bmd_job_state_for_resume()
    test_parse_vasp_result_files_uses_vasprun_dos_for_dos_workflow()
    test_parse_vasp_result_files_uses_vasprun_band_structure_for_band_workflow()
    print("results smoke test passed")
