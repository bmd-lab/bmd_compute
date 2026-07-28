from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from main import build_submission_state, build_workflow
from starlette.requests import Request


poscar = """Si
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

structure = parse_structure(poscar)

static_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE),
    potcar_functional="PBE_64",
)
assert "ENCUT = 620.0" in static_preview["incar"]
assert "ISPIN = 1" in static_preview["incar"]
assert "MAGMOM" not in static_preview["incar"]
assert "Gamma" in static_preview["kpoints"]
assert "Si2" in static_preview["poscar"]
assert "direct" in static_preview["poscar"].lower()
assert "0.2500000000000000    0.2500000000000000    0.2500000000000000 Si" in static_preview["poscar"]

spin_static_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SPIN_POLARIZED}),
    potcar_functional="PBE_64",
)
assert "ISPIN = 2" in spin_static_preview["incar"]
assert "MAGMOM = 2*0.6" in spin_static_preview["incar"]
assert spin_static_preview["kpoints"] == static_preview["kpoints"]
assert spin_static_preview["poscar"] == static_preview["poscar"]

relax_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.RELAX, Theory.PBE),
    potcar_functional="PBE_64",
)
assert "ENCUT = 580.0" in relax_preview["incar"]
assert "ISIF = 3" in relax_preview["incar"]
assert "ISPIN = 1" in relax_preview["incar"]
assert "MAGMOM" not in relax_preview["incar"]

summary, calculation, generated_inputs, submission_spec = build_submission_state(
    structure_text=poscar,
    fmt="poscar",
    calculation_spec=CalculationSpec(Purpose.STATIC, Theory.PBE),
    timestamp="20260728-120000",
)
assert summary["formula"] == "Si2"
assert calculation["calculation_type"] == "Static Energy"
assert generated_inputs["incar"] == static_preview["incar"]
assert submission_spec["flow_spec"]["calculation_spec"]["purpose"] == "static"

request = Request(
    {
        "type": "http",
        "method": "POST",
        "path": "/build-calculation",
        "headers": [],
    }
)
response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=None,
    workflow=None,
    method=None,
)
assert response.status_code == 200
assert response.context["generated_inputs"]["incar"] == static_preview["incar"]
assert response.context["generated_inputs"]["kpoints"] == static_preview["kpoints"]
assert response.context["generated_inputs"]["poscar"] == static_preview["poscar"]
assert response.context["submission_spec"]["flow_spec"]["calculation_spec"] == {
    "purpose": "static",
    "theory": "pbe",
    "modifiers": [],
    "label": None,
}

spin_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["spin_polarized"],
    workflow=None,
    method=None,
)
assert spin_response.status_code == 200
assert "ISPIN = 2" in spin_response.context["generated_inputs"]["incar"]
assert "MAGMOM = 2*0.6" in spin_response.context["generated_inputs"]["incar"]

relax_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="relax",
    theory="pbe",
    modifiers=None,
    workflow=None,
    method=None,
)
assert relax_response.status_code == 200
assert relax_response.context["calculation"]["calculation_type"] == "Geometry Optimisation"
assert "ENCUT = 580.0" in relax_response.context["generated_inputs"]["incar"]
assert "ISIF = 3" in relax_response.context["generated_inputs"]["incar"]
assert relax_response.context["submission_spec"]["flow_spec"]["calculation_spec"] == {
    "purpose": "relax",
    "theory": "pbe",
    "modifiers": [],
    "label": None,
}

template_source = open("templates/index.html", encoding="utf-8").read()
for heading in (
    "Scientific Summary",
    "Generated Inputs",
    "Computational Resources",
    "Ready for Submission",
):
    assert heading in template_source
assert "Calculation Summary" not in template_source
assert "Submission Preview" not in template_source
assert "input-tab-poscar" in template_source
assert "tab-panel-poscar" in template_source
assert "generated_inputs.poscar" in template_source
assert "input-tab-potcar" not in template_source
assert "tab-panel-potcar" not in template_source
assert "generated_inputs.potcar" not in template_source
assert "POTCAR Species" in template_source
assert 'id="calculation-review-form"' in template_source
assert 'data-auto-rebuild="{% if calculation %}true{% else %}false{% endif %}"' in template_source
assert "requestSubmit" in template_source
assert "data-current-calculation-action" in template_source

print("generated inputs smoke test passed")
