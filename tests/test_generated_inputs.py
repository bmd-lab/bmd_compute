import json

from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.calculations.resources import ALLOWED_MEMORY_GB, ALLOWED_QUEUES, ExecutionResources
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


def workflow_spec_json(stage_type, *, theory="pbe", modifiers=None, recipe=None):
    return json.dumps(
        {
            "stages": [
                {
                    "stage_type": stage_type,
                    "theory": theory,
                    "modifiers": list(modifiers or []),
                    "label": None,
                    "options": {},
                }
            ],
            "label": None,
            "recipe": recipe,
        },
        sort_keys=True,
    )


static_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE),
    potcar_functional="PBE_64",
)
assert "ENCUT = 620.0" in static_preview["incar"]
assert "NCORE = 8" in static_preview["incar"]
assert "ISPIN = 2" in static_preview["incar"]
assert "MAGMOM = 2*0.6" in static_preview["incar"]
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

try:
    preview_generated_inputs(
        structure,
        CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SOC}),
        potcar_functional="PBE_64",
    )
except CalculationValidationError as exc:
    assert "Spin-Orbit Coupling (SOC)" in exc.message
else:
    raise AssertionError("SOC should remain unavailable until vasp_ncl execution is validated.")

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

fe2o3_poscar = """Fe2O3
5.04
1.0 0.0 0.0
-0.5 0.8660254 0.0
0.0 0.0 2.7281746
Fe O
2 3
direct
0.0 0.0 0.355
0.0 0.0 0.645
0.305 0.0 0.25
0.0 0.305 0.25
0.695 0.695 0.25
"""
fe2o3_structure = parse_structure(fe2o3_poscar)
fe2o3_plain_preview = preview_generated_inputs(
    fe2o3_structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE),
    potcar_functional="PBE_64",
)
assert "ISPIN = 2" in fe2o3_plain_preview["incar"]
assert "MAGMOM = 2*5.0 3*0.6" in fe2o3_plain_preview["incar"]
for dft_u_key in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ", "LDAUPRINT", "LMAXMIX"):
    assert f"{dft_u_key} =" not in fe2o3_plain_preview["incar"]
fe2o3_dft_u_preview = preview_generated_inputs(
    fe2o3_structure,
    CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.DFT_U}),
    potcar_functional="PBE_64",
)
assert "ISPIN = 2" in fe2o3_dft_u_preview["incar"]
assert "MAGMOM = 2*5.0 3*0.6" in fe2o3_dft_u_preview["incar"]
assert "LDAU = True" in fe2o3_dft_u_preview["incar"]
assert "LDAUU = 5.3 0" in fe2o3_dft_u_preview["incar"]

relax_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.RELAX, Theory.PBE),
    potcar_functional="PBE_64",
)
assert "ENCUT = 580.0" in relax_preview["incar"]
assert "ISIF = 3" in relax_preview["incar"]
assert "ISPIN = 2" in relax_preview["incar"]
assert "MAGMOM = 2*0.6" in relax_preview["incar"]

band_preview = preview_generated_inputs(
    structure,
    CalculationSpec(Purpose.BAND_STRUCTURE, Theory.PBE),
    resources=ExecutionResources(cpus=24),
    potcar_functional="PBE_64",
)
band_relax_section, band_static_and_path = band_preview["incar"].split("\n\n", 1)
band_static_section, band_section = band_static_and_path.split("\n\n", 1)
assert "# Stage 1 - Geometry Optimisation" in band_relax_section
assert "# Stage 2 - Static Energy" in band_static_section
assert "# Stage 3 - Band Structure" in band_section
assert "NCORE = 8" in band_relax_section
assert "NCORE = 8" in band_static_section
assert "NCORE" not in band_section

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
assert "#SBATCH -J vasp_run_static" in generated_inputs["slurm_script"]
assert f"#SBATCH --nodes={DEFAULT_RESOURCES['nodes']}" in generated_inputs["slurm_script"]
assert f"#SBATCH --ntasks={DEFAULT_RESOURCES['ntasks']}" in generated_inputs["slurm_script"]
assert f"#SBATCH --mem={DEFAULT_RESOURCES['mem_gb']}G" in generated_inputs["slurm_script"]
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
    workflow_spec_json=workflow_spec_json("static"),
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
    queue=DEFAULT_PARTITION,
    workflow_spec_json=workflow_spec_json("static"),
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
    "allowed_memory_gb": list(ALLOWED_MEMORY_GB),
    "walltime": "12:00:00",
    "queue": DEFAULT_PARTITION,
    "allowed_queues": list(ALLOWED_QUEUES),
}
assert resource_response.context["submission_spec"]["resources"]["ntasks"] == 48
assert resource_response.context["submission_spec"]["resources"]["mem_gb"] == 256
assert resource_response.context["submission_spec"]["cluster"]["partition"] == DEFAULT_PARTITION
assert resource_response.context["submission_spec"]["cluster"]["account"] == DEFAULT_ACCOUNT
assert resource_response.context["generated_inputs"]["slurm_script"] == build_slurm_preview_script(
    resource_response.context["submission_spec"]
)
assert f"#SBATCH -p {DEFAULT_PARTITION}" in resource_response.context["generated_inputs"]["slurm_script"]
assert f"#SBATCH --account={DEFAULT_ACCOUNT}" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH -J vasp_run_static" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --nodes=1" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --ntasks=48" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --mem=256G" in resource_response.context["generated_inputs"]["slurm_script"]
assert "#SBATCH --time=12:00:00" in resource_response.context["generated_inputs"]["slurm_script"]

invalid_memory_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=None,
    cpus="48",
    memory_gb="100",
    walltime="12:00:00",
    queue=DEFAULT_PARTITION,
    workflow_spec_json=workflow_spec_json("static"),
    workflow=None,
    method=None,
)
assert invalid_memory_response.status_code == 400
assert "Memory must be one of" in invalid_memory_response.context["calculation_error"]["message"]

invalid_queue_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=None,
    cpus="48",
    memory_gb="256",
    walltime="12:00:00",
    queue="debug; rm -rf /",
    workflow_spec_json=workflow_spec_json("static"),
    workflow=None,
    method=None,
)
assert invalid_queue_response.status_code == 400
assert "Queue must be one of" in invalid_queue_response.context["calculation_error"]["message"]

spin_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["spin_polarized"],
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow_spec_json=workflow_spec_json("static", modifiers=["spin_polarized"]),
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
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow_spec_json=workflow_spec_json("static", modifiers=["soc"]),
    workflow=None,
    method=None,
)
assert soc_response.status_code == 400
assert "Spin-Orbit Coupling (SOC)" in soc_response.context["calculation_error"]["message"]

gamma_response = build_workflow(
    request,
    structure=poscar,
    fmt="poscar",
    purpose="static",
    theory="pbe",
    modifiers=["gamma_only"],
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow_spec_json=workflow_spec_json("static", modifiers=["gamma_only"]),
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
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow_spec_json=workflow_spec_json("static", modifiers=["dft_u"]),
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
    cpus=None,
    memory_gb=None,
    walltime=None,
    queue=None,
    workflow_spec_json=workflow_spec_json("relax"),
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
calculation_definition = template_source[
    template_source.index('<form\n                id="calculation-review-form"'):
]
resource_panel = calculation_definition[
    calculation_definition.index("<h3>Execution Resources</h3>"):
    calculation_definition.index("<h3>Scientific Specification</h3>")
]
queue_label_index = resource_panel.index("<label>Queue</label>")
queue_select_index = resource_panel.index('<select name="queue">')
queue_select_end = resource_panel.index("</select>", queue_select_index)
queue_select_block = resource_panel[queue_select_index:queue_select_end]
assert queue_label_index < queue_select_index
assert "selected_resources.allowed_queues" in queue_select_block
assert 'value="{{ queue }}"' in queue_select_block
assert "{% if selected_resources.queue == queue %}selected{% endif %}" in queue_select_block
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
assert "Ionic Convergence" in template_source

print("generated inputs smoke test passed")


def test_generated_inputs_smoke_module_loaded():
    assert response.status_code == 200
