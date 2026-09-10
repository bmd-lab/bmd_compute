from __future__ import annotations

import json

import main
from backend.calculations.models import (
    CalculationSpec,
    Modifier,
    Purpose,
    StageSpec,
    StageType,
    Theory,
    WorkflowSpec,
)
from backend.calculations.registry import calculation_form_options, validate_workflow_spec
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from starlette.requests import Request


SI_POSCAR = """Si
5.43
0.0 0.5 0.5
0.5 0.0 0.5
0.5 0.5 0.0
Si
2
direct
0.0 0.0 0.0
0.25 0.25 0.25
"""


def _analyze_html() -> str:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/analyze",
            "headers": [],
        }
    )
    response = main.analyze(request, structure=SI_POSCAR, fmt="poscar")
    return response.template.render(response.context)


def _mesh_lines(kpoints_text: str) -> list[list[str]]:
    return [line.split() for line in kpoints_text.splitlines()]


def test_gamma_only_is_not_rendered_in_normal_advanced_options():
    options = calculation_form_options()
    modifier_values = [option["value"] for option in options["modifiers"]]
    modifier_labels = [option["label"] for option in options["modifiers"]]

    assert "gamma_only" not in modifier_values
    assert "Gamma-only" not in modifier_labels
    assert {"spin_polarized", "soc", "dft_u", "dispersion"}.issubset(
        modifier_values
    )

    html = _analyze_html()
    assert "Gamma-only" not in html
    assert "gamma_only" not in html
    for label in (
        "Spin Polarised",
        "Spin-Orbit Coupling (SOC)",
        "DFT+U",
        "Dispersion correction",
    ):
        assert label in html


def test_normal_kpoint_policy_still_samples_small_si_cell():
    structure = parse_structure(SI_POSCAR)

    preview = preview_generated_inputs(
        structure,
        CalculationSpec(Purpose.STATIC, Theory.PBE),
        potcar_functional="PBE_64",
    )

    assert "pymatgen with grid density = 793 / number of atoms" in preview["kpoints"]
    assert ["7", "7", "7"] in _mesh_lines(preview["kpoints"])


def test_normal_kpoint_policy_can_still_naturally_generate_gamma_mesh():
    structure = parse_structure(SI_POSCAR)
    supercell = structure.copy()
    supercell.make_supercell([8, 8, 8])

    preview = preview_generated_inputs(
        supercell,
        CalculationSpec(Purpose.STATIC, Theory.PBE),
        potcar_functional="PBE_64",
    )

    assert "pymatgen with grid density = 793 / number of atoms" in preview["kpoints"]
    assert ["1", "1", "1"] in _mesh_lines(preview["kpoints"])


def test_backend_gamma_only_modifier_remains_available_programmatically():
    structure = parse_structure(SI_POSCAR)
    normal_preview = preview_generated_inputs(
        structure,
        CalculationSpec(Purpose.STATIC, Theory.PBE),
        potcar_functional="PBE_64",
    )
    gamma_preview = preview_generated_inputs(
        structure,
        CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.GAMMA_ONLY}),
        potcar_functional="PBE_64",
    )

    assert "pymatgen with grid density = 1 / number of atoms" in gamma_preview["kpoints"]
    assert ["1", "1", "1"] in _mesh_lines(gamma_preview["kpoints"])
    assert gamma_preview["vasp_executable"] == normal_preview["vasp_executable"]
    assert gamma_preview["poscar"] == normal_preview["poscar"]


def test_serialized_workflow_specs_preserve_gamma_only_for_compatibility():
    workflow = WorkflowSpec(
        [
            StageSpec(
                StageType.STATIC,
                Theory.PBE,
                {Modifier.GAMMA_ONLY},
            )
        ],
        recipe="static",
    )

    serialized = json.dumps(workflow.to_dict(), sort_keys=True)
    round_tripped = WorkflowSpec.from_dict(json.loads(serialized))

    assert validate_workflow_spec(round_tripped) == workflow
    assert round_tripped.stages[0].modifiers == frozenset({Modifier.GAMMA_ONLY})
