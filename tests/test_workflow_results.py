from __future__ import annotations

import json

from backend.calculations.models import (
    CalculationSpec,
    Purpose,
    StageSpec,
    StageType,
    Theory,
    WorkflowSpec,
)
from backend.workflow_results import (
    render_workflow_results,
    workflow_result_file_keys,
    workflow_result_parse_dos,
    workflow_result_parse_eigenvalues,
)


class FakeSpin:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __str__(self):
        return self.name


class FakeTotalDos:
    energies = [-2.0, -1.0, 0.0, 1.0]
    efermi = -0.5
    densities = {
        FakeSpin("up", 1): [0.0, 1.0, 2.0, 1.0],
        FakeSpin("down", -1): [0.0, 0.5, 1.5, 0.5],
    }


class FakeVasprun:
    tdos = FakeTotalDos()
    efermi = -0.5


class FakeKpoint:
    def __init__(self, label=None):
        self.label = label


class FakeBandStructure:
    efermi = 0.5
    distance = [0.0, 1.25, 2.5]
    kpoints = [
        FakeKpoint("\\Gamma"),
        FakeKpoint("K|\\Gamma"),
        FakeKpoint("X"),
    ]
    bands = {
        FakeSpin("up", 1): [
            [0.0, 1.0, 2.0],
            [2.0, 3.0, 4.0],
        ],
        FakeSpin("down", -1): [
            [0.25, 1.25, 2.25],
            [2.25, 3.25, 4.25],
        ],
    }

    def get_band_gap(self):
        return {
            "energy": 1.25,
            "direct": False,
        }

    def is_metal(self):
        return False


class FakeBandVasprun:
    efermi = 0.5
    calls = []

    def get_band_structure(self, **kwargs):
        self.calls.append(kwargs)
        return FakeBandStructure()


class FakeNonSpinBandStructure:
    efermi = 0.25
    distance = [0.0, 1.0, 2.0]
    kpoints = [
        FakeKpoint("Gamma"),
        FakeKpoint("K|U"),
        FakeKpoint("L"),
    ]
    bands = {
        FakeSpin("up", 1): [
            [0.0, 1.0, 2.0],
            [2.0, 3.0, 4.0],
        ],
    }

    def get_band_gap(self):
        return {
            "energy": 0.75,
            "direct": True,
        }

    def is_metal(self):
        return False


class FakeNonSpinBandVasprun:
    efermi = 0.25
    calls = []

    def get_band_structure(self, **kwargs):
        self.calls.append(kwargs)
        return FakeNonSpinBandStructure()


class FakeWideBandStructure:
    efermi = 0.0
    distance = [0.0, 1.0, 2.0]
    kpoints = [
        FakeKpoint("\\Gamma"),
        FakeKpoint("X"),
        FakeKpoint("L"),
    ]
    bands = {
        FakeSpin("up", 1): [
            [-12.0, 0.0, 12.0],
            [-4.0, 2.0, 31.0],
        ],
    }

    def get_band_gap(self):
        return {
            "energy": 1.0,
            "direct": True,
        }

    def is_metal(self):
        return False


class FakeWideBandVasprun:
    efermi = 0.0

    def get_band_structure(self, **kwargs):
        return FakeWideBandStructure()


dos_spec = CalculationSpec(Purpose.DOS, Theory.PBE)
band_spec = CalculationSpec(Purpose.BAND_STRUCTURE, Theory.PBE)
hse_band_workflow = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.HSE06),
        StageSpec(StageType.BAND_STRUCTURE, Theory.HSE06),
    ],
    recipe="custom",
)
static_spec = CalculationSpec(Purpose.STATIC, Theory.PBE)
relax_static_spec = CalculationSpec(Purpose.RELAX_STATIC, Theory.HSE06)

assert workflow_result_file_keys(static_spec) == ()
assert workflow_result_file_keys(relax_static_spec) == ()
assert workflow_result_file_keys(dos_spec) == ("doscar",)
assert workflow_result_file_keys(band_spec) == ("kpoints",)
assert not workflow_result_parse_dos(static_spec)
assert not workflow_result_parse_dos(relax_static_spec)
assert workflow_result_parse_dos(dos_spec)
assert not workflow_result_parse_dos(band_spec)
assert not workflow_result_parse_eigenvalues(static_spec)
assert not workflow_result_parse_eigenvalues(relax_static_spec)
assert workflow_result_parse_eigenvalues(band_spec)

payload = render_workflow_results(
    dos_spec,
    vasprun=FakeVasprun(),
    files={"doscar": {"path": "/remote/run/stage_03/DOSCAR"}},
)

assert payload.summaries["dos"] == {
    "available": True,
    "source": "vasprun.xml",
    "doscar": "/remote/run/stage_03/DOSCAR",
    "energy_points": 4,
    "energy_min_ev": -2.0,
    "energy_max_ev": 1.0,
    "spin_channels": 2,
    "fermi_level_ev": -0.5,
}
assert len(payload.visualizations) == 1

visualization = payload.visualizations[0]
assert visualization["id"] == "dos"
assert visualization["kind"] == "line_plot"
assert visualization["title"] == "Density of States"
assert visualization["download_filename"] == "density_of_states.png"
assert visualization["plot"]["source"] == "vasprun.xml"
assert visualization["plot"]["xaxis_title"] == "Energy - E_F (eV)"
assert "yaxis_range" not in visualization["plot"]
assert visualization["plot"]["x"] == [-1.5, -0.5, 0.5, 1.5]
assert visualization["plot"]["reference_axis"] == "x"
assert visualization["plot"]["traces"][0]["name"] == "Spin up"
assert visualization["plot"]["traces"][0]["y"] == [0.0, 1.0, 2.0, 1.0]
assert visualization["plot"]["traces"][1]["name"] == "Spin down"
assert visualization["plot"]["traces"][1]["y"] == [-0.0, -0.5, -1.5, -0.5]
json.dumps(payload.visualizations)

FakeBandVasprun.calls = []
band_payload = render_workflow_results(
    band_spec,
    vasprun=FakeBandVasprun(),
    files={"kpoints": {"path": "/remote/run/stage_03/KPOINTS"}},
    local_paths={"kpoints": "KPOINTS"},
)
assert FakeBandVasprun.calls[-1] == {
    "line_mode": True,
    "kpoints_filename": "KPOINTS",
}
assert band_payload.summaries["band_structure"] == {
    "available": True,
    "source": "vasprun.xml",
    "kpoints": "/remote/run/stage_03/KPOINTS",
    "bands": 2,
    "kpoints_count": 3,
    "spin_channels": 2,
    "fermi_level_ev": 0.5,
    "band_gap_ev": 1.25,
    "is_metal": False,
    "direct_gap": False,
}
assert len(band_payload.visualizations) == 1

band_visualization = band_payload.visualizations[0]
assert band_visualization["id"] == "band_structure"
assert band_visualization["kind"] == "line_plot"
assert band_visualization["title"] == "Band Structure"
assert band_visualization["download_filename"] == "band_structure.png"
assert band_visualization["plot"]["source"] == "vasprun.xml"
assert band_visualization["plot"]["xaxis_title"] == "K-point path"
assert band_visualization["plot"]["yaxis_title"] == "Energy - E_F (eV)"
assert band_visualization["plot"]["yaxis_range"] == [-10, 10]
assert band_visualization["plot"]["reference_axis"] == "y"
assert band_visualization["plot"]["x"] == [0.0, 1.25, 2.5]
assert band_visualization["plot"]["tickvals"] == [0.0, 1.25, 2.5]
assert band_visualization["plot"]["ticktext"] == ["Γ", "K|Γ", "X"]
assert band_visualization["plot"]["vertical_lines"] == [0.0, 1.25, 2.5]
assert band_visualization["plot"]["traces"][0]["name"] == "Spin up"
assert band_visualization["plot"]["traces"][0]["showlegend"] is True
assert band_visualization["plot"]["traces"][0]["y"] == [-0.5, 0.5, 1.5]
assert band_visualization["plot"]["traces"][1]["name"] == "Spin up"
assert band_visualization["plot"]["traces"][1]["showlegend"] is False
assert band_visualization["plot"]["traces"][2]["name"] == "Spin down"
assert band_visualization["plot"]["traces"][2]["dash"] == "dash"
assert band_visualization["plot"]["traces"][2]["y"] == [-0.25, 0.75, 1.75]
json.dumps(band_payload.visualizations)

FakeNonSpinBandVasprun.calls = []
non_spin_band_payload = render_workflow_results(
    band_spec,
    vasprun=FakeNonSpinBandVasprun(),
    files={"kpoints": {"path": "/remote/run/stage_03/KPOINTS"}},
    local_paths={"kpoints": "KPOINTS"},
)
non_spin_plot = non_spin_band_payload.visualizations[0]["plot"]
assert non_spin_band_payload.summaries["band_structure"]["spin_channels"] == 1
assert non_spin_plot["ticktext"] == ["Γ", "K|U", "L"]
assert non_spin_plot["traces"][0]["name"] == "Bands"
assert non_spin_plot["traces"][0]["showlegend"] is False
assert non_spin_plot["traces"][1]["name"] == "Bands"
assert non_spin_plot["traces"][1]["showlegend"] is False
assert all(trace["name"] != "Spin up" for trace in non_spin_plot["traces"])
json.dumps(non_spin_band_payload.visualizations)

wide_band_payload = render_workflow_results(
    band_spec,
    vasprun=FakeWideBandVasprun(),
    files={"kpoints": {"path": "/remote/run/stage_03/KPOINTS"}},
)
wide_band_plot = wide_band_payload.visualizations[0]["plot"]
assert wide_band_plot["yaxis_range"] == [-10, 10]
assert wide_band_plot["traces"][0]["y"] == [-12.0, 0.0, 12.0]
assert wide_band_plot["traces"][1]["y"] == [-4.0, 2.0, 31.0]
assert any(
    value > 10.0 or value < -10.0
    for trace in wide_band_plot["traces"]
    for value in trace["y"]
)

hse_wide_band_payload = render_workflow_results(
    hse_band_workflow,
    vasprun=FakeWideBandVasprun(),
    files={"kpoints": {"path": "/remote/run/stage_03/KPOINTS"}},
)
assert hse_wide_band_payload.visualizations[0]["plot"]["yaxis_range"] == [-10, 10]
assert (
    hse_wide_band_payload.visualizations[0]["plot"]["traces"]
    == wide_band_plot["traces"]
)

static_payload = render_workflow_results(
    static_spec,
    vasprun=FakeVasprun(),
    files={},
)
assert static_payload.summaries == {}
assert static_payload.visualizations == []

relax_static_payload = render_workflow_results(
    relax_static_spec,
    vasprun=FakeVasprun(),
    files={},
)
assert relax_static_payload.summaries == {}
assert relax_static_payload.visualizations == []

print("workflow results smoke test passed")
