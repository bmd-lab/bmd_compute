from backend.config import (
    DEFAULT_ACCOUNT,
    DEFAULT_FLOWS_DIR,
    DEFAULT_JOBFLOW_CONFIG_FILE,
    DEFAULT_LOGS_DIR,
    DEFAULT_PARTITION,
    DEFAULT_POTCAR_DIR,
    DEFAULT_REMOTE_PYTHON,
    DEFAULT_REMOTE_WORKSPACE_ROOT,
    DEFAULT_RESOURCES,
    DEFAULT_SHARED_POTCAR_ROOT,
    DEFAULT_VASP_CMD,
    MODULES,
    POTCAR_LINK_MAP,
)
from backend.submission import (
    build_backend_module_sources,
    build_execution_module_source,
    build_run_job_script,
    build_sbatch_script,
    create_submission_spec,
    default_resources_for_workflow,
    summarize_potcar_species,
)


flow_spec = {
    "workflow": "static",
    "potcar_functional": "PBE_64",
    "kpoints": None,
    "incar": {},
    "structure": {"type": "parsed"},
}


class FakeStructure:
    types_of_species = ["Ti", "O"]


spec = create_submission_spec(
    flow_spec,
    label="Si static!",
    timestamp="20260629-120000",
    env={},
)

assert spec["status"] == "pending"
assert spec["label"] == "Si-static"
assert spec["run_name"] == "Si-static-20260629-120000"
assert spec["paths"]["run_dir"] == f"{DEFAULT_FLOWS_DIR}/Si-static-20260629-120000"
assert DEFAULT_FLOWS_DIR == f"{DEFAULT_REMOTE_WORKSPACE_ROOT}/flows"
assert spec["paths"]["run_dir"].startswith("/bmd-db/guest/flows/")
assert spec["paths"]["log_out"] == f"{DEFAULT_LOGS_DIR}/Si-static-20260629-120000.out"
assert spec["paths"]["log_err"] == f"{DEFAULT_LOGS_DIR}/Si-static-20260629-120000.err"
assert spec["paths"]["slurm_out"] == f"{DEFAULT_LOGS_DIR}/Si-static-20260629-120000.slurm.out"
assert spec["paths"]["slurm_err"] == f"{DEFAULT_LOGS_DIR}/Si-static-20260629-120000.slurm.err"
assert spec["cluster"]["partition"] == DEFAULT_PARTITION
assert spec["cluster"]["account"] == DEFAULT_ACCOUNT
assert spec["resources"] == DEFAULT_RESOURCES
assert spec["environment"]["VASP_CMD"] == DEFAULT_VASP_CMD
assert spec["environment"]["JOBFLOW_CONFIG_FILE"] == DEFAULT_JOBFLOW_CONFIG_FILE
assert spec["environment"]["PMG_VASP_PSP_DIR"] == DEFAULT_POTCAR_DIR
assert spec["environment"]["PMG_VASP_PSP_DIR"] == DEFAULT_SHARED_POTCAR_ROOT
assert spec["paths"]["potcars_dir"] == DEFAULT_SHARED_POTCAR_ROOT
assert "/bmd-db/guest/potcars" not in spec["environment"]["PMG_VASP_PSP_DIR"]
assert DEFAULT_SHARED_POTCAR_ROOT not in spec["paths"]["directories_to_prepare"]
assert f"{DEFAULT_SHARED_POTCAR_ROOT}/PBE_64" not in spec["paths"]["directories_to_prepare"]
assert spec["environment"]["CUSTODIAN_NO_GZIP"] == "1"
assert spec["environment"]["ATOMATE2_VASP_ZIP_FILES"] == "False"
assert spec["modules"]["load"] == MODULES
assert spec["runner"]["python"] == DEFAULT_REMOTE_PYTHON
assert spec["runner"]["script_name"] == "run_job.py"
assert spec["runner"]["submission_spec_name"] == "submission.json"
assert spec["runner"]["backend_package_dir"] == "backend"
assert spec["runner"]["execution_module_name"] == "execution.py"
assert spec["potcar"]["repository"] == "shared"
assert spec["potcar"]["target"] == f"{DEFAULT_POTCAR_DIR}/PBE_64"
assert spec["potcar"]["symlink_targets"] == []
assert spec["potcar"]["species"] == []
assert spec["flow_spec"]["calculation_spec"] == {
    "purpose": "static",
    "theory": "pbe",
    "modifiers": [],
    "label": None,
}
assert spec["preflight"]["requires_remote_structure_check"] is False
assert spec["submission"]["ready"] is True
assert spec["submission"]["submitted"] is False

spin_flow_spec = {
    **flow_spec,
    "calculation_spec": {
        "purpose": "static",
        "theory": "pbe",
        "modifiers": ["spin_polarized"],
    },
}
spin_spec = create_submission_spec(
    spin_flow_spec,
    label="Si spin static!",
    timestamp="20260629-120000",
    env={},
)
assert spin_spec["flow_spec"]["calculation_spec"] == {
    "purpose": "static",
    "theory": "pbe",
    "modifiers": ["spin_polarized"],
    "label": None,
}

advanced_flow_spec = {
    **flow_spec,
    "calculation_spec": {
        "purpose": "static",
        "theory": "pbe",
        "modifiers": ["dft_u", "gamma_only", "soc", "spin_polarized"],
    },
}
advanced_spec = create_submission_spec(
    advanced_flow_spec,
    label="Advanced static!",
    timestamp="20260629-120000",
    env={},
)
assert advanced_spec["flow_spec"]["calculation_spec"] == {
    "purpose": "static",
    "theory": "pbe",
    "modifiers": ["dft_u", "gamma_only", "soc", "spin_polarized"],
    "label": None,
}

private_potcars_dir = "/private/bmd-potcars"
private_potcars_spec = create_submission_spec(
    flow_spec,
    label="Si static!",
    timestamp="20260629-120000",
    env={},
    potcars_dir=private_potcars_dir,
)
assert private_potcars_spec["potcar"]["repository"] == "private"
assert private_potcars_spec["potcar"]["target"] == f"{private_potcars_dir}/PBE_64"
assert private_potcars_spec["potcar"]["symlink_targets"] == [
    f"{private_potcars_dir}/{link_name}"
    for link_name in POTCAR_LINK_MAP["PBE_64"]
]
assert private_potcars_dir in private_potcars_spec["paths"]["directories_to_prepare"]
assert f"{private_potcars_dir}/PBE_64" in private_potcars_spec["paths"]["directories_to_prepare"]

env_spec = create_submission_spec(
    flow_spec,
    timestamp="20260629-120000",
    env={
        "SLURM_PARTITION": "debug",
        "SLURM_ACCOUNT": "debug-users",
        "VASP_CMD": "srun -n $SLURM_NTASKS vasp_std",
    },
)

assert env_spec["cluster"]["partition"] == "debug"
assert env_spec["cluster"]["account"] == "debug-users"
assert env_spec["environment"]["VASP_CMD"] == "srun -n $SLURM_NTASKS vasp_std"

assert default_resources_for_workflow("gw_static")["ntasks"] == 12
assert default_resources_for_workflow("gw_static")["mem_gb"] == 240
assert default_resources_for_workflow("relax_static_bands")["mem_gb"] == 160

potcar_summary = summarize_potcar_species(FakeStructure(), "PBE_64")
assert potcar_summary["species"] == [
    {"species": "Ti", "potcar_symbol": "Ti_pv"},
    {"species": "O", "potcar_symbol": "O"},
]

structure_spec = create_submission_spec(
    flow_spec,
    structure=FakeStructure(),
    timestamp="20260629-120000",
    env={},
)
assert structure_spec["potcar"]["species"] == potcar_summary["species"]
assert structure_spec["potcar"]["symbols"] == ["Ti_pv", "O"]

run_job_script = build_run_job_script(spec)
assert "__SPEC_JSON__" not in run_job_script
assert "json.load(handle)" in run_job_script
assert "BMD_SUBMISSION_SPEC" in run_job_script
assert "Si-static-20260629-120000" not in run_job_script
assert "from backend.execution import run_submission" in run_job_script
assert "run_submission(spec)" in run_job_script
assert "RelaxMaker" not in run_job_script
assert "StaticSetGenerator" not in run_job_script

sbatch_script = build_sbatch_script(spec)
assert "export PMG_VASP_PSP_DIR=/bmd-db/potcars" in sbatch_script
assert "/bmd-db/guest/potcars" not in sbatch_script
assert "if ! type module >/dev/null 2>&1; then" in sbatch_script
assert "source /etc/bashrc" in sbatch_script
assert "source /usr/share/lmod/lmod/init/bash" not in sbatch_script
assert (
    sbatch_script.index("source /etc/bashrc")
    < sbatch_script.index("module purge")
)
assert "module purge\n" in sbatch_script
for module_name in MODULES:
    assert f"module load {module_name}\n" in sbatch_script
    assert f"module load {module_name} >/dev/null" not in sbatch_script
    assert f"module load {module_name} || true" not in sbatch_script
assert 'echo "[sbatch debug] PATH=$PATH"' in sbatch_script
assert "module list" in sbatch_script
assert "which vasp_std" in sbatch_script
assert "which mpirun" in sbatch_script
assert f"which {DEFAULT_REMOTE_PYTHON}" in sbatch_script
assert f"{DEFAULT_REMOTE_PYTHON} --version" in sbatch_script
assert f"{DEFAULT_REMOTE_PYTHON} -u run_job.py" in sbatch_script
assert "\npython --version" not in sbatch_script
assert "/bmd/lee/envs" not in sbatch_script

execution_module_source = build_execution_module_source()
assert "def run_submission(spec: dict)" in execution_module_source
assert "structure_from_spec" in execution_module_source
assert "build_atomate2_flow_from_spec" in execution_module_source
assert "run_locally" in execution_module_source
assert "def incar_static" not in execution_module_source
assert "def incar_relax" not in execution_module_source
assert "def ksettings" not in execution_module_source
assert "RelaxMaker" not in execution_module_source
assert "StaticSetGenerator" not in execution_module_source

backend_module_sources = build_backend_module_sources()
assert set(backend_module_sources) == {
    "calculations/__init__.py",
    "calculations/builder.py",
    "calculations/models.py",
    "calculations/registry.py",
    "execution.py",
    "parser.py",
    "workflows.py",
}
assert "class CalculationSpec" in backend_module_sources["calculations/models.py"]
assert "def calculation_spec_from_flow_spec" in backend_module_sources["calculations/registry.py"]
assert "def structure_from_spec" in backend_module_sources["parser.py"]
assert "def build_atomate2_flow_from_spec" in backend_module_sources["workflows.py"]
assert "RelaxMaker" in backend_module_sources["workflows.py"]
assert "StaticSetGenerator" in backend_module_sources["workflows.py"]

print("submission spec smoke test passed")
