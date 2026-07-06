from backend.submission import (
    build_backend_module_sources,
    build_execution_module_source,
    build_run_job_script,
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
assert spec["paths"]["run_dir"] == "/bmd-db/lee/flows/Si-static-20260629-120000"
assert spec["paths"]["log_out"] == "/bmd-db/lee/logs/Si-static-20260629-120000.out"
assert spec["paths"]["log_err"] == "/bmd-db/lee/logs/Si-static-20260629-120000.err"
assert spec["paths"]["slurm_out"] == "/bmd-db/lee/logs/Si-static-20260629-120000.slurm.out"
assert spec["paths"]["slurm_err"] == "/bmd-db/lee/logs/Si-static-20260629-120000.slurm.err"
assert spec["cluster"]["partition"] == "leeburton-pool"
assert spec["cluster"]["account"] == "power-leeburton-users_v2"
assert spec["resources"] == {
    "nodes": 1,
    "ntasks": 48,
    "mem_gb": 128,
    "walltime": "72:00:00",
}
assert spec["environment"]["VASP_CMD"] == "mpirun -n $SLURM_NTASKS vasp_std"
assert spec["environment"]["JOBFLOW_CONFIG_FILE"] == "/bmd-db/lee/jobflow_minimal.yaml"
assert spec["environment"]["PMG_VASP_PSP_DIR"] == "/bmd-db/lee/potcars"
assert spec["environment"]["CUSTODIAN_NO_GZIP"] == "1"
assert spec["environment"]["ATOMATE2_VASP_ZIP_FILES"] == "False"
assert spec["modules"]["load"] == [
    "intel/rocky8-oneAPI-2023",
    "vasp/rocky8-intel-6.4.1",
]
assert spec["runner"]["script_name"] == "run_job.py"
assert spec["runner"]["submission_spec_name"] == "submission.json"
assert spec["runner"]["backend_package_dir"] == "backend"
assert spec["runner"]["execution_module_name"] == "execution.py"
assert spec["potcar"]["target"] == "/bmd-db/lee/potcars/PBE_64"
assert spec["potcar"]["symlink_targets"] == [
    "/bmd-db/lee/potcars/POT_GGA_PAW_PBE_64",
    "/bmd-db/lee/potcars/POT_PAW_PBE_64",
]
assert spec["potcar"]["species"] == []
assert spec["preflight"]["requires_remote_structure_check"] is False
assert spec["submission"]["ready"] is True
assert spec["submission"]["submitted"] is False

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
assert set(backend_module_sources) == {"execution.py", "parser.py", "workflows.py"}
assert "def structure_from_spec" in backend_module_sources["parser.py"]
assert "def build_atomate2_flow_from_spec" in backend_module_sources["workflows.py"]
assert "RelaxMaker" in backend_module_sources["workflows.py"]
assert "StaticSetGenerator" in backend_module_sources["workflows.py"]

print("submission spec smoke test passed")
