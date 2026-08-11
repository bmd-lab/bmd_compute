from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.calculations.resources import ExecutionResources
from backend.calculations.registry import CalculationValidationError
from backend.config import DEFAULT_ACCOUNT, DEFAULT_PARTITION, DEFAULT_RESOURCES
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from backend.submission import build_slurm_preview_script
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
assert "NCORE = 8" in static_preview["incar"]
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

high_cpu_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE),
    resources=ExecutionResources(cpus=48),
    potcar_functional="PBE_64",
)
assert high_cpu_preview["incar"] == static_preview["incar"]

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
assert generated_inputs["slurm_script"] == build_slurm_preview_script(submission_spec)
assert "\r" not in generated_inputs["slurm_script"]
assert generated_inputs["slurm_script"].startswith("#!/bin/bash\n\n")
assert f"#SBATCH -p {DEFAULT_PARTITION}" in generated_inputs["slurm_script"]
assert f"#SBATCH --account={DEFAULT_ACCOUNT}" in generated_inputs["slurm_script"]
assert "#SBATCH -J Si-static" in generated_inputs["slurm_script"]
assert f"#SBATCH --nodes={DEFAULT_RESOURCES['nodes']}" in generated_inputs["slurm_script"]
assert f"#SBATCH --ntasks={DEFAULT_RESOURCES['ntasks']}" in generated_inputs["slurm_script"]
assert f"#SBATCH --mem={DEFAULT_RESOURCES['mem_gb']}GB" in generated_inputs["slurm_script"]
assert f"#SBATCH --time={DEFAULT_RESOURCES['walltime']}" in generated_inputs["slurm_script"]
assert "ulimit -s 81920" in generated_inputs["slurm_script"]
assert "module load intel/rocky8-oneAPI-2023" in generated_inputs["slurm_script"]
assert "module load vasp/rocky8-intel-6.4.1" in generated_inputs["slurm_script"]
assert "mpirun -n $SLURM_NTASKS vasp_std" in generated_inputs["slurm_script"]
for internal_detail in (
    "/bmd-db/guest",
    "run_job.py",
    "submission.json",
    "JOBFLOW_CONFIG_FILE",
    "PMG_VASP_PSP_DIR",
    "CUSTODIAN_",
    "ATOMATE2_",
    "BMD_SUBMISSION_SPEC",
    "echo ",
    "test -f",
    "backend",
):
    assert internal_detail not in generated_inputs["slurm_script"]
assert submission_spec["flow_spec"]["calculation_spec"]["purpose"] == "static"
assert submission_spec["flow_spec"]["execution_resources"]["ntasks"] == 24
assert submission_spec["resources"]["ntasks"] == 24

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
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow=None,
    method=None,
)
assert response.status_code == 200
assert response.context["generated_inputs"]["incar"] == static_preview["incar"]
assert response.context["generated_inputs"]["kpoints"] == static_preview["kpoints"]
assert response.context["generated_inputs"]["poscar"] == static_preview["poscar"]
assert response.context["generated_inputs"]["slurm_script"] == build_slurm_preview_script(
    response.context["submission_spec"]
)
assert response.context["submission_spec"]["flow_spec"]["calculation_spec"] == {
    "purpose": "static",
    "theory": "pbe",
    "modifiers": [],
    "label": None,
}
assert response.context["selected_resources"]["cpus"] == 24

resource_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=None,
    cpus="48",
    memory_gb="256",
    walltime="12:00:00",
    queue="debug",
    workflow=None,
    method=None,
)
assert resource_response.status_code == 200
assert resource_response.context["generated_inputs"]["incar"] == static_preview["incar"]
assert resource_response.context["selected_resources"] == {
    "nodes": 1,
    "cpus": 48,
    "allowed_cpu_counts": [24, 48, 72, 96, 120, 144, 168, 192],
    "memory_gb": 256,
    "walltime": "12:00:00",
    "queue": "debug",
}
assert resource_response.context["submission_spec"]["resources"]["ntasks"] == 48
assert resource_response.context["submission_spec"]["resources"]["mem_gb"] == 256
assert resource_response.context["submission_spec"]["cluster"]["partition"] == "debug"
assert resource_response.context["submission_spec"]["cluster"]["account"] == DEFAULT_ACCOUNT
assert resource_response.context["generated_inputs"]["slurm_script"] == build_slurm_preview_script(
    resource_response.context["submission_spec"]
)
assert "#SBATCH -p debug" in resource_response.context["generated_inputs"]["slurm_script"]
assert f"#SBATCH --account={DEFAULT_ACCOUNT}" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH -J Si-static" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --nodes=1" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --ntasks=48" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --mem=256GB" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --time=12:00:00" in resource_response.context["generated_inputs"]["slurm_script"]

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
    "Calculation Definition",
    "Scientific Specification",
    "Execution Resources",
    "Calculation Summary",
    "Generated Inputs",
    "Execution Summary",
    "Ready for Submission",
):
    assert heading in template_source
assert "Computational Resources" not in template_source
assert "Submission Preview" not in template_source
assert '<div class="advanced-options-field">' in template_source
assert "<label>Advanced Options</label>" in template_source
assert "<summary>Advanced Options</summary>" not in template_source
assert '<summary aria-label="Toggle advanced options"></summary>' in template_source
assert '<div class="callout-title">Calculation Plan</div>' in template_source
assert '<div class="callout-title">Jobs</div>' not in template_source
assert "calculation.job_names" not in template_source
for field_name in ("cpus", "memory_gb", "walltime", "queue"):
    assert f'name="{field_name}"' in template_source
assert 'name="account"' not in template_source
assert "Account" not in template_source
assert "submission_spec.cluster.account" not in template_source
assert "input-tab-poscar" in template_source
assert "tab-panel-poscar" in template_source
assert "generated_inputs.poscar" in template_source
assert "input-tab-slurm" in template_source
assert "tab-panel-slurm" in template_source
assert "generated_inputs.slurm_script" in template_source
assert "SLURM Script" in template_source
assert template_source.count("data-copy-input") == 5
assert template_source.count('class="input-copy-button" data-copy-input') == 4
assert "navigator.clipboard.writeText" in template_source
assert "\\u2713 Copied" in template_source
assert "Not supported" in template_source
assert "input-tab-potcar" not in template_source
assert "tab-panel-potcar" not in template_source
assert "generated_inputs.potcar" not in template_source
assert "POTCAR Species" in template_source
assert 'id="calculation-review-form"' in template_source
assert "data-auto-rebuild" not in template_source
assert "requestSubmit" not in template_source
assert "data-current-calculation-action" in template_source

print("generated inputs smoke test passed")
