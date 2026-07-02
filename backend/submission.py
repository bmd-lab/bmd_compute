from __future__ import annotations

import os
import posixpath
import re
import shlex
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

SUBMISSION_ENV_KEYS = (
    "VASP_CMD",
    "JOBFLOW_CONFIG_FILE",
    "PMG_VASP_PSP_DIR",
    "CUSTODIAN_NO_GZIP",
    "ATOMATE2_VASP_ZIP_FILES",
)

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


def _shell_export(name: str, value: str | None) -> str:
    if value is None:
        return ""
    return f"export {name}={shlex.quote(str(value))}"


def _module_lines(submission_spec: dict) -> list[str]:
    modules = submission_spec.get("modules", {})
    lines = []

    if modules.get("purge_first"):
        lines.append("module purge >/dev/null 2>&1 || true")

    for module_name in modules.get("load", []):
        lines.append(f"module load {shlex.quote(str(module_name))} >/dev/null 2>&1 || true")

    return lines


def _submission_env_exports(submission_spec: dict) -> list[str]:
    environment = submission_spec.get("environment", {})
    return [
        _shell_export(key, environment.get(key))
        for key in SUBMISSION_ENV_KEYS
        if environment.get(key) is not None
    ]


def _remote_runner_python() -> str:
    return r'''
import json
import os
import sys
import traceback

print("[runner] python:", sys.version.replace("\n", " "))

def _pkg_ver(name):
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "<no __version__>")
        print(f"[runner] {name} version:", version)
    except Exception as exc:
        print(f"[runner] {name} import failed:", exc)

for package in ("atomate2", "jobflow", "pymatgen", "custodian"):
    _pkg_ver(package)

spec = json.loads(__SPEC_JSON__)
flow_spec = spec["flow_spec"]
workflow = (flow_spec.get("workflow") or "static").lower()
potcar_functional = flow_spec.get("potcar_functional") or "PBE_64"
incar = flow_spec.get("incar") or flow_spec.get("incar_overrides") or {}
kpoints_config = flow_spec.get("kpoints")

ENCUT_STATIC_FINAL_DEFAULT = 620
ENCUT_RELAX_DEFAULT = 580

def _is_hse_incar(settings):
    if not isinstance(settings, dict):
        return False
    value = settings.get("LHFCALC")
    if isinstance(value, str) and value.strip().strip(".").upper() in ("T", "TRUE", "YES"):
        return True
    if value is True:
        return True
    for key in ("AEXX", "HFSCREEN", "ALDAC", "LHFCALC_HYBRID"):
        if key in settings:
            return True
    gga = settings.get("GGA")
    return isinstance(gga, str) and "HSE" in gga.upper()

def incar_static(settings, allow_ncore=True):
    user_settings = dict(settings or {})
    for key in ("GGA", "ENAUG", "LMIXTAU"):
        user_settings.setdefault(key, None)
    user_settings.setdefault("LWAVE", False)
    user_settings.setdefault("LCHARG", True)
    user_settings.setdefault("ISMEAR", -5)
    user_settings.setdefault("SIGMA", 0.05)
    user_settings.setdefault("NEDOS", 4001)
    user_settings.setdefault("LORBIT", 11)
    user_settings.setdefault("LREAL", False)
    user_settings.setdefault("PREC", "Accurate")
    user_settings.setdefault("ADDGRID", True)
    try:
        encut_now = int(float(user_settings.get("ENCUT", 0)))
    except Exception:
        encut_now = 0
    user_settings["ENCUT"] = max(encut_now, ENCUT_STATIC_FINAL_DEFAULT)
    if allow_ncore and not _is_hse_incar(user_settings):
        user_settings.setdefault("NCORE", 2)
    return user_settings

def incar_relax(settings, user=None):
    user_settings = dict(settings or {})
    explicit_settings = dict(user or {})
    if "LCHARG" not in explicit_settings:
        user_settings["LCHARG"] = False
    if "LWAVE" not in explicit_settings:
        user_settings["LWAVE"] = False
    for key in ("LAECHG", "LVTOT", "LELF", "LVHAR", "LORBIT"):
        if key not in explicit_settings:
            user_settings[key] = None
    for key in ("GGA", "ENAUG", "LMIXTAU"):
        user_settings.setdefault(key, None)
    user_settings.setdefault("ALGO", "Fast")
    user_settings.setdefault("ADDGRID", True)
    user_settings.setdefault("EDIFFG", -0.01)
    if _is_hse_incar(user_settings) or _is_hse_incar(explicit_settings):
        user_settings.setdefault("PRECFOCK", "Fast")
        user_settings.setdefault("ALGO", "Damped")
    if not _is_hse_incar(user_settings) and not _is_hse_incar(explicit_settings):
        user_settings.setdefault("NCORE", 2)
    return user_settings

def ksettings(structure, kpoints):
    if not kpoints:
        return None
    mode = kpoints.get("mode")
    value = kpoints.get("value")
    if mode == "mesh":
        from pymatgen.io.vasp.inputs import Kpoints
        nx, ny, nz = (int(value[0]), int(value[1]), int(value[2]))
        natoms = len(structure)
        target = (nx, ny, nz)
        kppa = max(1, nx * ny * nz * max(1, natoms))
        seen = set()
        found = None
        def mesh_for(candidate_kppa):
            kp = Kpoints.automatic_density(structure, int(max(1, candidate_kppa)))
            if kp.kpts:
                mesh = kp.kpts[0]
                if isinstance(mesh, (list, tuple)) and len(mesh) >= 3:
                    return (int(mesh[0]), int(mesh[1]), int(mesh[2]))
            return (0, 0, 0)
        for _ in range(64):
            mesh = mesh_for(kppa)
            if mesh == target:
                found = kppa
                break
            if mesh in seen:
                break
            seen.add(mesh)
            target_product = nx * ny * nz
            mesh_product = max(1, mesh[0] * mesh[1] * mesh[2])
            scale = max(
                target_product / mesh_product,
                nx / max(1, mesh[0]),
                ny / max(1, mesh[1]),
                nz / max(1, mesh[2]),
            )
            kppa = int(max(1, kppa * (1.25 if scale < 1 else min(3.0, 1.15 * scale))))
        return {"grid_density": float(found if found is not None else kppa)}
    return {mode: float(value)}

def build_structure(structure_spec):
    from pymatgen.core import Lattice, Structure
    kind = structure_spec.get("type")
    if kind == "path":
        return Structure.from_file(structure_spec["path"])
    if kind == "pasted_text":
        fmt = structure_spec.get("format") or "poscar"
        return Structure.from_str(structure_spec["text"], fmt=fmt)
    if kind == "builder":
        import numpy as np
        lattice_spec = structure_spec.get("lattice", {}) or {}
        lattice = Lattice.from_parameters(
            float(lattice_spec.get("a", 3.84)),
            float(lattice_spec.get("b", 3.84)),
            float(lattice_spec.get("c", 3.84)),
            float(lattice_spec.get("alpha", 120.0)),
            float(lattice_spec.get("beta", 90.0)),
            float(lattice_spec.get("gamma", 60.0)),
        )
        coords = structure_spec["coords"]
        if structure_spec.get("coord_kind", "frac") == "cart":
            inv = np.linalg.inv(lattice.matrix.T)
            coords = [list(inv.dot(np.array(coord, float))) for coord in coords]
        return Structure(lattice, structure_spec["species"], coords)
    if kind == "mp":
        from mp_api.client import MPRester
        key = os.environ.get("MP_API_KEY")
        if not key:
            raise RuntimeError("MP_API_KEY not set")
        with MPRester(key) as mpr:
            result = mpr.materials.summary.search(material_ids=[structure_spec["query"]])
            if not result:
                raise RuntimeError(f"MP-ID not found: {structure_spec['query']}")
            structure = result[0].structure
        if structure_spec.get("conventional", True):
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
            structure = SpacegroupAnalyzer(structure, symprec=1e-3).get_conventional_standard_structure(
                international_monoclinic=True
            )
        if structure_spec.get("symmetrize", False):
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
            structure = SpacegroupAnalyzer(structure, symprec=1e-3).get_refined_structure()
        return structure
    raise RuntimeError(f"Unsupported structure spec: {structure_spec}")

try:
    structure = build_structure(flow_spec["structure"])
    kpoints = ksettings(structure, kpoints_config)

    from atomate2.vasp.jobs.core import RelaxMaker, StaticMaker
    from atomate2.vasp.sets.core import RelaxSetGenerator, StaticSetGenerator
    from jobflow import Flow
    from jobflow.managers.local import run_locally

    if workflow == "static":
        generator = StaticSetGenerator(
            user_potcar_functional=potcar_functional,
            user_kpoints_settings=kpoints,
            user_incar_settings=incar_static(incar),
        )
        maker = StaticMaker(input_set_generator=generator, name="static")
    elif workflow in ("relax", "relax_ions"):
        user_incar = dict(incar)
        user_incar.setdefault("ENCUT", ENCUT_RELAX_DEFAULT)
        user_incar.setdefault("ISPIN", 2)
        user_incar.setdefault("EDIFF", 1e-6)
        user_incar.setdefault("ADDGRID", True)
        user_incar.setdefault("EDIFFG", -0.01)
        if workflow == "relax_ions":
            user_incar["ISIF"] = 2
        generator = RelaxSetGenerator(
            user_potcar_functional=potcar_functional,
            user_kpoints_settings=kpoints,
            user_incar_settings=incar_relax(user_incar, user=incar),
        )
        maker = RelaxMaker(
            input_set_generator=generator,
            name=("relax_ions" if workflow == "relax_ions" else "relax"),
        )
    else:
        raise RuntimeError(f"Unsupported workflow for BMD Compute submission: {workflow}")

    flow = Flow([maker.make(structure)], name=spec["run_name"])
    try:
        run_locally(flow, ensure_success=True, create_folders=False)
    except TypeError:
        run_locally(flow, ensure_success=True)
    print("JOBFLOW_LOCAL_DONE")
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
'''.strip()


def build_job_body(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    runner = submission_spec["runner"]
    environment = submission_spec["environment"]
    spec_json = json_dumps_for_shell(submission_spec)
    job_python = _remote_runner_python().replace("__SPEC_JSON__", repr(spec_json))
    mp_api_key = submission_spec.get("preflight", {}).get("mp_api_key")
    mp_export = (
        f"export MP_API_KEY={shlex.quote(str(mp_api_key))}"
        if mp_api_key
        else 'echo "[sbatch] MP_API_KEY not provided for this run."'
    )

    psp_dir = environment.get("PMG_VASP_PSP_DIR")
    jobflow_config = environment.get("JOBFLOW_CONFIG_FILE")

    return f"""
set -e -o pipefail
mkdir -p {shlex.quote(paths["run_dir"])}
cd {shlex.quote(paths["run_dir"])}
{mp_export}
export CUSTODIAN_NO_GZIP=1
export ATOMATE2_VASP_ZIP_FILES=False
{_shell_export("VASP_CMD", environment.get("VASP_CMD"))}
{_shell_export("PMG_VASP_PSP_DIR", psp_dir) if psp_dir else 'echo "[sbatch] PMG_VASP_PSP_DIR not set"'}
{_shell_export("JOBFLOW_CONFIG_FILE", jobflow_config) if jobflow_config else "true"}
echo "[sbatch] Using partition={submission_spec["cluster"]["partition"]} account={submission_spec["cluster"]["account"]}"
echo "[sbatch] VASP_CMD=$VASP_CMD"
echo "[sbatch] SLURM_NTASKS=${{SLURM_NTASKS:-<unset>}}"
which srun 2>/dev/null || true; srun --version 2>/dev/null | head -n1 || true
which {shlex.quote(runner["python"])} || true
python --version || true
cat > {shlex.quote(runner["script_name"])} <<'PY'
{job_python}
PY
{shlex.quote(runner["python"])} -u {shlex.quote(runner["script_name"])} 1>{shlex.quote(runner["stdout"])} 2>{shlex.quote(runner["stderr"])}
echo "Done. Logs:"; echo {shlex.quote(runner["stdout"])}; echo {shlex.quote(runner["stderr"])}
""".lstrip()


def build_sbatch_script(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    run_name = submission_spec["run_name"]
    module_lines = _module_lines(submission_spec)
    exports = _submission_env_exports(submission_spec)
    job_body = build_job_body(submission_spec)

    header = (
        f"#SBATCH --job-name={run_name}\n"
        f"#SBATCH --output={paths['slurm_out']}\n"
        f"#SBATCH --error={paths['slurm_err']}"
    )

    body = f"""#!/usr/bin/env bash
{header}
set -e -o pipefail

# -- quiet module loads (compute node) --
{os.linesep.join(module_lines)}

# -- reasonable stack size for VASP --
ulimit -s 81920 || true

# -- propagate environment expected by the runner --
{os.linesep.join(exports)}

# -- POTCAR sanity (warn and show layout) --
echo "PMG_VASP_PSP_DIR=$PMG_VASP_PSP_DIR"
ls -ld "$PMG_VASP_PSP_DIR"/POT_* >/dev/null 2>&1 || echo "[warn] No POT_* dir found under $PMG_VASP_PSP_DIR"

# -- runner body --
{job_body.strip()}
"""
    return body.rstrip() + "\n"


def build_submission_command(submission_spec: dict, *, dry_run: bool = False) -> str:
    paths = submission_spec["paths"]
    resources = submission_spec["resources"]
    cluster = submission_spec["cluster"]
    potcar = submission_spec["potcar"]
    sbatch_script = build_sbatch_script(submission_spec)
    pot_links = " ".join(shlex.quote(link) for link in potcar.get("symlink_targets", []))
    link_command = (
        f"for L in {pot_links}; do ln -sfn {shlex.quote(potcar['target'])} \"$L\"; done"
        if pot_links
        else "true"
    )
    sbatch_line = (
        f"sbatch -p {shlex.quote(str(cluster['partition']))} "
        f"-A {shlex.quote(str(cluster['account']))} "
        f"-N {int(resources['nodes'])} "
        f"-n {int(resources['ntasks'])} "
        f"--mem={int(resources['mem_gb'])}G "
        f"-t {shlex.quote(str(resources['walltime']))} "
        f"--parsable {shlex.quote(paths['remote_script'])}"
    )

    directories = " ".join(
        shlex.quote(path)
        for path in paths.get("directories_to_prepare", [])
    )

    if dry_run:
        return _build_verified_dry_run_command(paths, potcar, sbatch_script)

    command = f"""set -e -o pipefail
mkdir -p {directories}
{link_command}
cat > {shlex.quote(paths['remote_script'])} <<'SBATCH'
{sbatch_script}
SBATCH
"""

    return command + f"""\
echo "Submitting with: {sbatch_line}"
out=$({sbatch_line} 2>&1); rc=$?; echo "SBATCH_RAW_OUT=$out"; exit $rc
"""


def _build_verified_dry_run_command(paths: dict, potcar: dict, sbatch_script: str) -> str:
    parent_dirs = list(paths.get("directories_to_prepare", []))
    run_dir = paths["run_dir"]
    parent_dir_commands = "\n".join(
        _verified_mkdir_command(path, "Remote directories prepared")
        for path in parent_dirs
    )
    link_commands = "\n".join(
        _verified_symlink_command(potcar["target"], link, "POTCAR links prepared")
        for link in potcar.get("symlink_targets", [])
    ) or "true"

    return f"""set -e -o pipefail
prep_fail() {{
    echo "PREP_FAILED_STAGE=$1"
    echo "PREP_FAILED_REASON=$2"
    exit 42
}}
prep_ok() {{
    echo "PREP_OK=$1"
}}
verify_dir() {{
    test -d "$2" || prep_fail "$1" "Expected directory does not exist: $2"
}}
verify_file() {{
    test -f "$2" || prep_fail "$1" "Expected file does not exist: $2"
}}
verify_symlink() {{
    test -L "$3" || prep_fail "$1" "Expected POTCAR symlink does not exist: $3"
    target="$(readlink "$3" 2>/dev/null || true)"
    test "$target" = "$2" || prep_fail "$1" "POTCAR symlink points to $target instead of $2"
}}
{parent_dir_commands}
prep_ok "Remote directories prepared"
{_verified_mkdir_command(run_dir, "Working directory created")}
prep_ok "Working directory created"
{link_commands}
prep_ok "POTCAR links prepared"
{{
cat > {shlex.quote(paths['remote_script'])} <<'SBATCH'
{sbatch_script}
SBATCH
}} || prep_fail "Submission script written" "Unable to write submission script: {paths['remote_script']}"
verify_file "Submission script written" {shlex.quote(paths['remote_script'])}
prep_ok "Submission script written"
prep_ok "Ready for submission"
echo DRY RUN
exit 0
"""


def _verified_mkdir_command(path: str, stage: str) -> str:
    quoted_path = shlex.quote(path)
    quoted_stage = shlex.quote(stage)
    reason = shlex.quote(f"Unable to create directory: {path}")
    return (
        f"mkdir -p {quoted_path} || prep_fail {quoted_stage} {reason}\n"
        f"verify_dir {quoted_stage} {quoted_path}"
    )


def _verified_symlink_command(target: str, link: str, stage: str) -> str:
    quoted_target = shlex.quote(target)
    quoted_link = shlex.quote(link)
    quoted_stage = shlex.quote(stage)
    reason = shlex.quote(f"Unable to create POTCAR symlink: {link}")
    return (
        f"ln -sfn {quoted_target} {quoted_link} || prep_fail {quoted_stage} {reason}\n"
        f"verify_symlink {quoted_stage} {quoted_target} {quoted_link}"
    )


def json_dumps_for_shell(value: dict) -> str:
    import json

    return json.dumps(value)


def parse_sbatch_job_id(output: str) -> str:
    output = output or ""
    patterns = (
        r"(?m)^JOBID=(\d+)\b",
        r"(?m)^SBATCH_RAW_OUT=(\d+)\b",
        r"(?m)^(\d+)\b",
        r"Submitted batch job\s+(\d+)",
        r"\b(\d+)\b",
    )

    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)

    raise RuntimeError("Could not parse job id from sbatch output above.")


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
    "build_sbatch_script",
    "build_submission_command",
    "create_submission_spec",
    "default_resources_for_workflow",
    "parse_sbatch_job_id",
    "sanitize_label",
    "summarize_potcar_species",
]
