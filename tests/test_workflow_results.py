from __future__ import annotations

import json

from backend.calculations.models import CalculationSpec, Purpose, Theory
from backend.workflow_results import (
    render_workflow_results,
    workflow_result_file_keys,
    workflow_result_parse_dos,
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


dos_spec = CalculationSpec(Purpose.DOS, Theory.PBE)
static_spec = CalculationSpec(Purpose.STATIC, Theory.PBE)

assert workflow_result_file_keys(static_spec) == ()
assert workflow_result_file_keys(dos_spec) == ("doscar",)
assert not workflow_result_parse_dos(static_spec)
assert workflow_result_parse_dos(dos_spec)

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
assert visualization["plot"]["source"] == "vasprun.xml"
assert visualization["plot"]["xaxis_title"] == "Energy - E_F (eV)"
assert visualization["plot"]["x"] == [-1.5, -0.5, 0.5, 1.5]
assert visualization["plot"]["traces"][0]["name"] == "Spin up"
assert visualization["plot"]["traces"][0]["y"] == [0.0, 1.0, 2.0, 1.0]
assert visualization["plot"]["traces"][1]["name"] == "Spin down"
assert visualization["plot"]["traces"][1]["y"] == [-0.0, -0.5, -1.5, -0.5]
json.dumps(payload.visualizations)

static_payload = render_workflow_results(
    static_spec,
    vasprun=FakeVasprun(),
    files={},
)
assert static_payload.summaries == {}
assert static_payload.visualizations == []

print("workflow results smoke test passed")
