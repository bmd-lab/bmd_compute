from __future__ import annotations

import os
import posixpath
import re
import time
from copy import deepcopy


NOTEBOOK_DEFAULTS = {
    "remote_host": "powerslurm-login.tau.ac.il",
    "username": "leeburton",
    "port": 22,
    "keepalive_s": 30,
    "open_mongo_tunnel": True,
    "mongo_remote_host": "132.66.112.243",
    "mongo_remote_port": 27017,
    "mongo_local_port": 27017,
    "VASP_CMD": "mpirun -n $SLURM_NTASKS vasp_std",
    "JOBFLOW_CONFIG_FILE": "/bmd-db/lee/jobflow_minimal.yaml",
    "PMG_VASP_PSP_DIR": "/bmd-db/lee/potcars",
    "remote_env_dir": "/bmd/lee/envs/atomate2_remote",
    "flows_dir": "/bmd-db/lee/flows",
    "logs_dir": "/bmd-db/lee/logs",
}

DEFAULT_RESOURCES = {
    "nodes": 1,
    "ntasks": 24,
    "mem_gb": 120,
    "walltime": "72:00:00",
}

DEFAULT_PARTITION = "power-leeburton"
DEFAULT_ACCOUNT = "power-leeburton-users"
DEFAULT_POTCAR_FUNCTIONAL = "PBE_64"
DEFAULT_VASP_LAUNCHER = "srun --mpi=pmi2 -n $SLURM_NTASKS vasp_std"

MODULES = [
    "intel/rocky8-oneAPI-2023",
    "vasp/rocky8-intel-6.4.1",
]

POTCAR_LINK_MAP = {
    "PBE_64": ["POT_GGA_PAW_PBE_64", "POT_PAW_PBE_64"],
    "PBE_54": ["POT_GGA_PAW_PBE_54"],
    "PBE_52": ["POT_GGA_PAW_PBE_52", "POT_GGA_PAW_PBE"],
    "LDA": ["POT_LDA_PAW"],
}

MP_RECOMMENDED_POTCAR_SYMBOLS = {
    "Ba": "Ba_sv",
    "Be": "Be_sv",
    "Ca": "Ca_sv",
    "Cr": "Cr_pv",
    "Cs": "Cs_sv",
    "Cu": "Cu_pv",
    "Dy": "Dy_3",
    "Er": "Er_3",
    "Eu": "Eu",
    "Fe": "Fe_pv",
    "Ga": "Ga_d",
    "Gd": "Gd",
    "Ge": "Ge_d",
    "Hf": "Hf_pv",
    "Ho": "Ho_3",
    "In": "In_d",
    "K": "K_sv",
    "La": "La",
    "Li": "Li_sv",
    "Lu": "Lu_3",
    "Mg": "Mg_pv",
    "Mn": "Mn_pv",
    "Mo": "Mo_pv",
    "Na": "Na_pv",
    "Nb": "Nb_pv",
    "Nd": "Nd_3",
    "Ni": "Ni_pv",
    "Os": "Os_pv",
    "Pb": "Pb_d",
    "Pm": "Pm_3",
    "Pr": "Pr_3",
    "Rb": "Rb_sv",
    "Re": "Re_pv",
    "Rh": "Rh_pv",
    "Ru": "Ru_pv",
    "Sc": "Sc_sv",
    "Sm": "Sm_3",
    "Sn": "Sn_d",
    "Sr": "Sr_sv",
    "Ta": "Ta_pv",
    "Tb": "Tb_3",
    "Tc": "Tc_pv",
    "Ti": "Ti_pv",
    "Tl": "Tl_d",
    "Tm": "Tm_3",
    "V": "V_pv",
    "W": "W_pv",
    "Y": "Y_sv",
    "Yb": "Yb_2",
    "Zr": "Zr_sv",
}


def sanitize_label(label: str) -> str:
    label = (label or "").strip()
    label = re.sub(r"\s+", "-", label)
    label = re.sub(r"[^A-Za-z0-9._-]+", "", label)
    return (label or "vasp_run")[:60].rstrip("-_.") or "vasp_run"


def _coerce_int(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def _env_bin(remote_env_dir: str) -> str:
    path = (remote_env_dir or "").rstrip("/")
    return path + "/bin" if path else "/usr/bin"


def _first_nonempty(*values):
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value
        elif value is not None:
            return value
    return None


def default_resources_for_workflow(workflow: str | None = None) -> dict:
    resources = dict(DEFAULT_RESOURCES)
    workflow_name = (workflow or "").lower()

    if workflow_name in ("gw_static", "gw_static_bands_true"):
        resources["ntasks"] = 12
        resources["mem_gb"] = 240
    elif workflow_name == "relax_static_bands":
        resources["mem_gb"] = 160

    return resources


def _species_name(species) -> str:
    if isinstance(species, str):
        return species

    for attr in ("symbol", "species_string"):
        value = getattr(species, attr, None)
        if value:
            return str(value)

    return str(species)


def _structure_species(structure) -> list[str]:
    if structure is None:
        return []

    species = []
    raw_species = getattr(structure, "types_of_species", None)
    if raw_species:
        species = [_species_name(item) for item in raw_species]

    if not species:
        composition = getattr(structure, "composition", None)
        elements = getattr(composition, "elements", None)
        if elements:
            species = [_species_name(item) for item in elements]

    unique_species = []
    seen = set()
    for name in species:
        if name not in seen:
            unique_species.append(name)
            seen.add(name)

    return unique_species


def _pymatgen_potcar_symbols(structure, potcar_functional: str) -> list[str] | None:
    try:
        from pymatgen.io.vasp.sets import MPRelaxSet

        vasp_set = MPRelaxSet(
            structure,
            user_potcar_functional=potcar_functional,
        )
        return [str(symbol) for symbol in vasp_set.potcar_symbols]
    except Exception:
        return None


def summarize_potcar_species(structure, potcar_functional: str | None = None) -> dict:
    """
    Return a display-friendly species to POTCAR-symbol mapping.

    This uses pymatgen's VASP input-set metadata when available and falls back
    to the Materials Project-style recommended suffixes used by pymatgen. It
    does not read POTCAR files or inspect remote filesystem paths.
    """

    functional = potcar_functional or DEFAULT_POTCAR_FUNCTIONAL
    species = _structure_species(structure)
    symbols = _pymatgen_potcar_symbols(structure, functional) if structure is not None else None
    source = "pymatgen"

    if not symbols or len(symbols) != len(species):
        symbols = [
            MP_RECOMMENDED_POTCAR_SYMBOLS.get(name, name)
            for name in species
        ]
        source = "fallback"

    rows = [
        {
            "species": species_name,
            "potcar_symbol": symbol,
        }
        for species_name, symbol in zip(species, symbols)
    ]

    return {
        "functional": functional,
        "source": source,
        "species": rows,
        "symbols": list(symbols),
    }


def create_submission_spec(
    flow_spec: dict,
    *,
    structure=None,
    label: str = "vasp_run",
    timestamp: str | None = None,
    nodes=None,
    ntasks=None,
    mem_gb=None,
    walltime: str | None = None,
    username: str | None = None,
    flows_dir: str | None = None,
    logs_dir: str | None = None,
    potcars_dir: str | None = None,
    remote_env_dir: str | None = None,
    partition: str | None = None,
    account: str | None = None,
    vasp_cmd: str | None = None,
    jobflow_config_file: str | None = None,
    env: dict | None = None,
    mp_api_key: str | None = None,
) -> dict:
    """
    Describe a pending calculation using notebook submission defaults.

    This prepares the state that the notebook assembled immediately before
    writing a remote script and calling sbatch. It performs no submission,
    SSH, SLURM, remote execution, or Jobflow execution.
    """

    env_values = env if env is not None else os.environ
    flow_spec_copy = deepcopy(flow_spec)
    workflow_name = (flow_spec_copy.get("workflow") or "static").lower()
    resource_defaults = default_resources_for_workflow(workflow_name)

    sanitized_label = sanitize_label(label)
    run_timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    run_name = f"{sanitized_label}-{run_timestamp}"

    resolved_username = username or NOTEBOOK_DEFAULTS["username"]
    resolved_flows_dir = flows_dir or NOTEBOOK_DEFAULTS["flows_dir"]
    resolved_logs_dir = logs_dir or NOTEBOOK_DEFAULTS["logs_dir"]
    resolved_potcars_dir = potcars_dir or NOTEBOOK_DEFAULTS["PMG_VASP_PSP_DIR"]
    resolved_remote_env_dir = remote_env_dir or NOTEBOOK_DEFAULTS["remote_env_dir"]

    resolved_nodes = _coerce_int(nodes, resource_defaults["nodes"])
    resolved_ntasks = _coerce_int(ntasks, resource_defaults["ntasks"])
    resolved_mem_gb = _coerce_int(mem_gb, resource_defaults["mem_gb"])
    resolved_walltime = walltime or resource_defaults["walltime"]

    resolved_partition = (
        partition
        or env_values.get("SLURM_PARTITION")
        or env_values.get("SLURM_DEFAULT_PARTITION")
        or DEFAULT_PARTITION
    )
    resolved_account = account or env_values.get("SLURM_ACCOUNT") or DEFAULT_ACCOUNT

    resolved_vasp_cmd = _first_nonempty(
        env_values.get("VASP_CMD"),
        vasp_cmd,
        NOTEBOOK_DEFAULTS["VASP_CMD"],
        DEFAULT_VASP_LAUNCHER,
    )
    resolved_jobflow_config = _first_nonempty(
        env_values.get("JOBFLOW_CONFIG_FILE"),
        jobflow_config_file,
        NOTEBOOK_DEFAULTS["JOBFLOW_CONFIG_FILE"],
    )
    resolved_pmg_psp_dir = _first_nonempty(
        env_values.get("PMG_VASP_PSP_DIR"),
        resolved_potcars_dir,
    )

    potcar_functional = flow_spec_copy.get("potcar_functional", DEFAULT_POTCAR_FUNCTIONAL)
    potcar_species = summarize_potcar_species(structure, potcar_functional)
    potcar_target = posixpath.join(resolved_potcars_dir, potcar_functional)
    potcar_links = [
        posixpath.join(resolved_potcars_dir, link_name)
        for link_name in POTCAR_LINK_MAP.get(potcar_functional, [])
    ]

    run_dir = posixpath.join(resolved_flows_dir, run_name)
    log_out = posixpath.join(resolved_logs_dir, f"{run_name}.out")
    log_err = posixpath.join(resolved_logs_dir, f"{run_name}.err")
    slurm_out = posixpath.join(resolved_logs_dir, f"{run_name}.slurm.out")
    slurm_err = posixpath.join(resolved_logs_dir, f"{run_name}.slurm.err")
    remote_script = posixpath.join(resolved_flows_dir, f"{run_name}.sbatch.sh")

    return {
        "status": "pending",
        "label": sanitized_label,
        "run_name": run_name,
        "created_at": run_timestamp,
        "flow_spec": flow_spec_copy,
        "paths": {
            "run_dir": run_dir,
            "remote_script": remote_script,
            "logs_dir": resolved_logs_dir,
            "flows_dir": resolved_flows_dir,
            "potcars_dir": resolved_potcars_dir,
            "log_out": log_out,
            "log_err": log_err,
            "slurm_out": slurm_out,
            "slurm_err": slurm_err,
            "directories_to_prepare": [
                resolved_flows_dir,
                resolved_logs_dir,
                resolved_potcars_dir,
                potcar_target,
            ],
        },
        "cluster": {
            "remote_host": NOTEBOOK_DEFAULTS["remote_host"],
            "username": resolved_username,
            "port": NOTEBOOK_DEFAULTS["port"],
            "partition": resolved_partition,
            "account": resolved_account,
        },
        "resources": {
            "nodes": resolved_nodes,
            "ntasks": resolved_ntasks,
            "mem_gb": resolved_mem_gb,
            "walltime": resolved_walltime,
        },
        "environment": {
            "VASP_CMD": resolved_vasp_cmd,
            "JOBFLOW_CONFIG_FILE": resolved_jobflow_config,
            "PMG_VASP_PSP_DIR": resolved_pmg_psp_dir,
            "CUSTODIAN_NO_GZIP": "1",
            "ATOMATE2_VASP_ZIP_FILES": "False",
            "ATOMATE2_REMOTE_ENV": resolved_remote_env_dir,
        },
        "modules": {
            "purge_first": True,
            "load": list(MODULES),
        },
        "runner": {
            "working_directory": run_dir,
            "python": posixpath.join(_env_bin(resolved_remote_env_dir), "python"),
            "script_name": "run_job.py",
            "stdout": log_out,
            "stderr": log_err,
        },
        "potcar": {
            "functional": potcar_functional,
            "species": potcar_species["species"],
            "symbols": potcar_species["symbols"],
            "symbol_source": potcar_species["source"],
            "target": potcar_target,
            "symlink_targets": potcar_links,
        },
        "preflight": {
            "requires_remote_structure_check": flow_spec_copy.get("structure", {}).get("type") == "path",
            "mp_api_key_provided": bool((mp_api_key or "").strip()),
        },
        "submission": {
            "method": "sbatch",
            "ready": True,
            "submitted": False,
            "reason": "Specification only; no submission has been performed.",
        },
    }


__all__ = [
    "DEFAULT_ACCOUNT",
    "DEFAULT_PARTITION",
    "DEFAULT_RESOURCES",
    "MODULES",
    "NOTEBOOK_DEFAULTS",
    "POTCAR_LINK_MAP",
    "create_submission_spec",
    "default_resources_for_workflow",
    "sanitize_label",
    "summarize_potcar_species",
]
