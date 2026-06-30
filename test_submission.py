from backend.submission import create_submission_spec, default_resources_for_workflow


flow_spec = {
    "workflow": "static",
    "potcar_functional": "PBE_64",
    "kpoints": None,
    "incar": {},
    "structure": {"type": "parsed"},
}


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
assert spec["cluster"]["partition"] == "power-leeburton"
assert spec["cluster"]["account"] == "power-leeburton-users"
assert spec["resources"] == {
    "nodes": 1,
    "ntasks": 24,
    "mem_gb": 120,
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
assert spec["potcar"]["target"] == "/bmd-db/lee/potcars/PBE_64"
assert spec["potcar"]["symlink_targets"] == [
    "/bmd-db/lee/potcars/POT_GGA_PAW_PBE_64",
    "/bmd-db/lee/potcars/POT_PAW_PBE_64",
]
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

print("submission spec smoke test passed")
