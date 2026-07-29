from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.calculations.registry import CalculationValidationError
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

soc_static_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SOC}),
    potcar_functional="PBE_64",
)
assert "ISPIN = 2" in soc_static_preview["incar"]
assert "LSORBIT = True" in soc_static_preview["incar"]
assert "LNONCOLLINEAR = True" in soc_static_preview["incar"]
assert "ISYM = 0" in soc_static_preview["incar"]
assert "SAXIS = 0 0 1" in soc_static_preview["incar"]
assert "MAGMOM" not in soc_static_preview["incar"]

gamma_static_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.GAMMA_ONLY}),
    potcar_functional="PBE_64",
)
assert "Gamma" in gamma_static_preview["kpoints"]
assert any(
    line.split() == ["1", "1", "1"]
    for line in gamma_static_preview["kpoints"].splitlines()
)
assert gamma_static_preview["poscar"] == static_preview["poscar"]

try:
    preview_generated_inputs(
        structure,
        CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.DFT_U}),
        potcar_functional="PBE_64",
    )
except CalculationValidationError as exc:
    assert "DFT+U was requested" in exc.message
else:
    raise AssertionError("DFT+U should fail clearly when no U values are available.")

nio_poscar = """NiO
4.3
1.0 0.0 0.0
0.0 1.0 0.0
0.0 0.0 1.0
Ni O
1 1
direct
0.0 0.0 0.0
0.5 0.5 0.5
"""
nio_structure = parse_structure(nio_poscar)
non_dft_u_nio_preview = preview_generated_inputs(
    nio_structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE),
    potcar_functional="PBE_64",
)
for dft_u_key in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ", "LDAUPRINT", "LMAXMIX"):
    assert f"{dft_u_key} =" not in non_dft_u_nio_preview["incar"]
dft_u_preview = preview_generated_inputs(
    nio_structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.DFT_U}),
    potcar_functional="PBE_64",
)
assert "LDAU = True" in dft_u_preview["incar"]
assert "LDAUU" in dft_u_preview["incar"]

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

soc_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["soc"],
    workflow=None,
    method=None,
)
assert soc_response.status_code == 200
assert "LSORBIT = True" in soc_response.context["generated_inputs"]["incar"]

gamma_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["gamma_only"],
    workflow=None,
    method=None,
)
assert gamma_response.status_code == 200
assert any(
    line.split() == ["1", "1", "1"]
    for line in gamma_response.context["generated_inputs"]["kpoints"].splitlines()
)

dft_u_error_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["dft_u"],
    workflow=None,
    method=None,
)
assert dft_u_error_response.status_code == 400
assert "DFT+U was requested" in dft_u_error_response.context["calculation_error"]["message"]

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
assert "data-auto-rebuild" not in template_source
assert "requestSubmit" not in template_source
assert "data-current-calculation-action" in template_source

print("generated inputs smoke test passed")
