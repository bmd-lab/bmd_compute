from __future__ import annotations

import os
import posixpath
import re
import shlex
import time
from copy import deepcopy
from pathlib import Path

from backend.calculations.models import CalculationSpec
from backend.calculations.registry import (
    calculation_spec_from_flow_spec,
    validate_calculation_spec,
)
from backend.config import (
    DEFAULT_ACCOUNT,
    DEFAULT_PARTITION,
    DEFAULT_POTCAR_FUNCTIONAL,
    DEFAULT_REMOTE_PYTHON,
    DEFAULT_RESOURCES,
    DEFAULT_SHARED_POTCAR_ROOT,
    DEFAULT_VASP_LAUNCHER,
    MODULES,
    NOTEBOOK_DEFAULTS,
    POTCAR_LINK_MAP,
    SUBMISSION_ENV_KEYS,
    WORKFLOW_RESOURCE_OVERRIDES,
)


SUBMISSION_SPEC_FILENAME = "submission.json"
REMOTE_BACKEND_PACKAGE_DIR = "backend"
REMOTE_EXECUTION_MODULE_FILENAME = "execution.py"
REMOTE_BACKEND_INIT_FILENAME = "__init__.py"
REMOTE_BACKEND_MODULE_FILENAMES = (
    REMOTE_EXECUTION_MODULE_FILENAME,
    "calculations/__init__.py",
    "calculations/builder.py",
    "calculations/models.py",
    "calculations/registry.py",
    "parser.py",
    "workflows.py",
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


def _remote_path_equal(left: str | None, right: str | None) -> bool:
    return posixpath.normpath(str(left or "").rstrip("/")) == posixpath.normpath(
        str(right or "").rstrip("/")
    )


def _uses_shared_potcar_repository(potcars_dir: str | None) -> bool:
    return _remote_path_equal(potcars_dir, DEFAULT_SHARED_POTCAR_ROOT)


def default_resources_for_workflow(workflow: str | None = None) -> dict:
    resources = dict(DEFAULT_RESOURCES)
    workflow_name = (workflow or "").lower()
    resources.update(WORKFLOW_RESOURCE_OVERRIDES.get(workflow_name, {}))

    return resources


def default_resources_for_calculation_spec(spec: CalculationSpec | None = None) -> dict:
    if spec is not None:
        validate_calculation_spec(spec)

    return dict(DEFAULT_RESOURCES)


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
        lines.append("module purge")

    for module_name in modules.get("load", []):
        lines.append(f"module load {shlex.quote(str(module_name))}")

    return lines


def _submission_env_exports(submission_spec: dict) -> list[str]:
    environment = submission_spec.get("environment", {})
    return [
        _shell_export(key, environment.get(key))
        for key in SUBMISSION_ENV_KEYS
        if environment.get(key) is not None
    ]


def _run_job_launcher_python() -> str:
    return r'''
import json
import os
import sys
import traceback

from backend.execution import run_submission


spec_path = os.environ.get("BMD_SUBMISSION_SPEC")
if not spec_path:
    spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "submission.json")
print("[runner] submission spec:", spec_path)
with open(spec_path, "r", encoding="utf-8") as handle:
    spec = json.load(handle)

try:
    run_submission(spec)
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
'''.strip()


def build_run_job_script(submission_spec: dict | None = None) -> str:
    del submission_spec
    return _run_job_launcher_python()


def build_execution_module_source() -> str:
    return build_backend_module_sources()[REMOTE_EXECUTION_MODULE_FILENAME]


def build_backend_module_sources() -> dict[str, str]:
    backend_dir = Path(__file__).resolve().parent
    return {
        filename: (backend_dir / filename).read_text(encoding="utf-8")
        for filename in REMOTE_BACKEND_MODULE_FILENAMES
    }


def _run_job_path(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    runner = submission_spec["runner"]
    script_name = runner["script_name"]

    if str(script_name).startswith("/"):
        return script_name

    return posixpath.join(paths["run_dir"], script_name)


def _submission_json_path(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    runner = submission_spec["runner"]
    spec_name = runner.get("submission_spec_name", SUBMISSION_SPEC_FILENAME)

    if str(spec_name).startswith("/"):
        return spec_name

    return posixpath.join(paths["run_dir"], spec_name)


def _remote_backend_dir(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    runner = submission_spec["runner"]
    backend_dir = runner.get("backend_package_dir", REMOTE_BACKEND_PACKAGE_DIR)

    if str(backend_dir).startswith("/"):
        return backend_dir

    return posixpath.join(paths["run_dir"], backend_dir)


def _execution_module_path(submission_spec: dict) -> str:
    runner = submission_spec["runner"]
    module_name = runner.get("execution_module_name", REMOTE_EXECUTION_MODULE_FILENAME)

    if str(module_name).startswith("/"):
        return module_name

    return posixpath.join(_remote_backend_dir(submission_spec), module_name)


def _backend_module_paths(submission_spec: dict) -> dict[str, str]:
    paths = {
        filename: posixpath.join(_remote_backend_dir(submission_spec), filename)
        for filename in REMOTE_BACKEND_MODULE_FILENAMES
    }
    paths[REMOTE_EXECUTION_MODULE_FILENAME] = _execution_module_path(submission_spec)
    return paths


def _backend_init_path(submission_spec: dict) -> str:
    runner = submission_spec["runner"]
    init_name = runner.get("backend_init_name", REMOTE_BACKEND_INIT_FILENAME)

    if str(init_name).startswith("/"):
        return init_name

    return posixpath.join(_remote_backend_dir(submission_spec), init_name)


def build_job_body(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    runner = submission_spec["runner"]
    environment = submission_spec["environment"]
    run_job_path = _run_job_path(submission_spec)
    submission_json_path = _submission_json_path(submission_spec)
    backend_module_paths = _backend_module_paths(submission_spec)
    backend_module_checks = "\n".join(
        (
            f"test -f {shlex.quote(path)} || "
            f'{{ echo "[sbatch] Missing execution module at {path}"; exit 1; }}'
        )
        if filename == REMOTE_EXECUTION_MODULE_FILENAME
        else (
            f"test -f {shlex.quote(path)} || "
            f'{{ echo "[sbatch] Missing backend module {filename} at {path}"; exit 1; }}'
        )
        for filename, path in backend_module_paths.items()
    )
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
{shlex.quote(runner["python"])} --version
test -f {shlex.quote(run_job_path)} || {{ echo "[sbatch] Missing run_job.py at {run_job_path}"; exit 1; }}
test -f {shlex.quote(submission_json_path)} || {{ echo "[sbatch] Missing submission.json at {submission_json_path}"; exit 1; }}
{backend_module_checks}
export BMD_SUBMISSION_SPEC={shlex.quote(submission_json_path)}
{shlex.quote(runner["python"])} -u {shlex.quote(runner["script_name"])} 1>{shlex.quote(runner["stdout"])} 2>{shlex.quote(runner["stderr"])}
echo "Done. Logs:"; echo {shlex.quote(runner["stdout"])}; echo {shlex.quote(runner["stderr"])}
""".lstrip()


def build_sbatch_script(submission_spec: dict) -> str:
    paths = submission_spec["paths"]
    run_name = submission_spec["run_name"]
    module_lines = _module_lines(submission_spec)
    exports = _submission_env_exports(submission_spec)
    job_body = build_job_body(submission_spec)
    module_block = "\n".join(module_lines)
    export_block = "\n".join(exports)

    header = (
        f"#SBATCH --job-name={run_name}\n"
        f"#SBATCH --output={paths['slurm_out']}\n"
        f"#SBATCH --error={paths['slurm_err']}"
    )

    body = f"""#!/usr/bin/env bash
{header}
set -e -o pipefail

# -- module loads (compute node, verbose diagnostics enabled) --
if ! type module >/dev/null 2>&1; then
    source /etc/bashrc
fi
{module_block}
echo "[sbatch debug] PATH=$PATH"
module list
which vasp_std
which mpirun

# -- reasonable stack size for VASP --
ulimit -s 81920 || true

# -- propagate environment expected by the runner --
{export_block}

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
    submission_json = json_dumps_for_remote_file(submission_spec)
    submission_json_path = _submission_json_path(submission_spec)
    backend_dir = _remote_backend_dir(submission_spec)
    backend_init_path = _backend_init_path(submission_spec)
    backend_module_paths = _backend_module_paths(submission_spec)
    backend_module_sources = build_backend_module_sources()
    run_job_script = build_run_job_script(submission_spec)
    run_job_path = _run_job_path(submission_spec)
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
        return _build_verified_dry_run_command(
            paths,
            potcar,
            submission_json_path,
            submission_json,
            backend_dir,
            backend_init_path,
            backend_module_paths,
            backend_module_sources,
            run_job_path,
            run_job_script,
            sbatch_script,
        )

    command = f"""set -e -o pipefail
mkdir -p {directories}
mkdir -p {shlex.quote(paths['run_dir'])}
mkdir -p {shlex.quote(backend_dir)}
{link_command}
cat > {shlex.quote(submission_json_path)} <<'JSON'
{submission_json}
JSON
test -f {shlex.quote(submission_json_path)}
cat > {shlex.quote(backend_init_path)} <<'PY'
PY
{_backend_module_write_commands(backend_module_paths, backend_module_sources)}
cat > {shlex.quote(run_job_path)} <<'PY'
{run_job_script}
PY
test -f {shlex.quote(run_job_path)}
cat > {shlex.quote(paths['remote_script'])} <<'SBATCH'
{sbatch_script}
SBATCH
test -f {shlex.quote(paths['remote_script'])}
echo "SBATCH_SCRIPT_PATH={paths['remote_script']}"
"""

    return command + f"""\
echo "Submitting with: {sbatch_line}"
set +e
out=$({sbatch_line} 2>&1)
rc=$?
set -e
echo "SBATCH_RAW_OUT=$out"
exit $rc
"""


def _build_verified_dry_run_command(
    paths: dict,
    potcar: dict,
    submission_json_path: str,
    submission_json: str,
    backend_dir: str,
    backend_init_path: str,
    backend_module_paths: dict[str, str],
    backend_module_sources: dict[str, str],
    run_job_path: str,
    run_job_script: str,
    sbatch_script: str,
) -> str:
    parent_dirs = list(paths.get("directories_to_prepare", []))
    run_dir = paths["run_dir"]
    execution_module_path = backend_module_paths[REMOTE_EXECUTION_MODULE_FILENAME]
    parent_dir_commands = "\n".join(
        _verified_mkdir_command(path, "Remote directories prepared")
        for path in parent_dirs
    )
    link_commands = "\n".join(
        _verified_symlink_command(potcar["target"], link, "POTCAR links prepared")
        for link in potcar.get("symlink_targets", [])
    )
    potcar_prep_block = (
        f"{link_commands}\nprep_ok \"POTCAR links prepared\""
        if link_commands
        else ""
    )

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
{{
cat > {shlex.quote(submission_json_path)} <<'JSON'
{submission_json}
JSON
}} || prep_fail "submission.json uploaded" "Unable to write submission.json: {submission_json_path}"
verify_file "submission.json uploaded" {shlex.quote(submission_json_path)}
prep_ok "submission.json uploaded"
{_verified_mkdir_command(backend_dir, "Execution module uploaded")}
{{
cat > {shlex.quote(backend_init_path)} <<'PY'
PY
{_backend_module_write_commands(backend_module_paths, backend_module_sources, verify=False)}
}} || prep_fail "Execution module uploaded" "Unable to write execution module: {execution_module_path}"
{_backend_module_verify_commands(backend_module_paths, "Execution module uploaded")}
prep_ok "Execution module uploaded"
{{
cat > {shlex.quote(run_job_path)} <<'PY'
{run_job_script}
PY
}} || prep_fail "run_job.py uploaded" "Unable to write run_job.py: {run_job_path}"
verify_file "run_job.py uploaded" {shlex.quote(run_job_path)}
prep_ok "run_job.py uploaded"
{potcar_prep_block}
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


def _backend_module_write_commands(
    module_paths: dict[str, str],
    module_sources: dict[str, str],
    *,
    verify: bool = True,
) -> str:
    lines = []
    for filename in REMOTE_BACKEND_MODULE_FILENAMES:
        path = module_paths[filename]
        source = module_sources[filename]
        parent = posixpath.dirname(path)
        if parent:
            lines.append(f"mkdir -p {shlex.quote(parent)}")
        lines.append(
            f"cat > {shlex.quote(path)} <<'PY'\n"
            f"{source}\n"
            "PY"
        )
        if verify:
            lines.append(f"test -f {shlex.quote(path)}")
    return "\n".join(lines)


def _backend_module_verify_commands(module_paths: dict[str, str], stage: str) -> str:
    return "\n".join(
        f"verify_file {shlex.quote(stage)} {shlex.quote(module_paths[filename])}"
        for filename in REMOTE_BACKEND_MODULE_FILENAMES
    )


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


def json_dumps_for_remote_file(value: dict) -> str:
    import json

    return json.dumps(value, indent=2)


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
    remote_python: str | None = None,
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
    calculation_spec = calculation_spec_from_flow_spec(flow_spec_copy)
    flow_spec_copy["calculation_spec"] = calculation_spec.to_dict()
    resource_defaults = default_resources_for_calculation_spec(calculation_spec)

    sanitized_label = sanitize_label(label)
    run_timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    run_name = f"{sanitized_label}-{run_timestamp}"

    resolved_username = username or NOTEBOOK_DEFAULTS["username"]
    resolved_flows_dir = flows_dir or NOTEBOOK_DEFAULTS["flows_dir"]
    resolved_logs_dir = logs_dir or NOTEBOOK_DEFAULTS["logs_dir"]
    resolved_potcars_dir = potcars_dir or NOTEBOOK_DEFAULTS["PMG_VASP_PSP_DIR"]
    resolved_remote_env_dir = remote_env_dir or NOTEBOOK_DEFAULTS["remote_env_dir"]
    resolved_remote_python = remote_python or NOTEBOOK_DEFAULTS.get("remote_python") or DEFAULT_REMOTE_PYTHON
    if remote_env_dir is not None and remote_python is None:
        resolved_remote_python = posixpath.join(_env_bin(resolved_remote_env_dir), "python")

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
    use_shared_potcars = _uses_shared_potcar_repository(resolved_potcars_dir)
    if use_shared_potcars:
        potcar_links = []
    else:
        potcar_links = [
            posixpath.join(resolved_potcars_dir, link_name)
            for link_name in POTCAR_LINK_MAP.get(potcar_functional, [])
        ]
    directories_to_prepare = [
        resolved_flows_dir,
        resolved_logs_dir,
    ]
    if potcar_links:
        directories_to_prepare.extend(
            [
                resolved_potcars_dir,
                potcar_target,
            ]
        )

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
            "directories_to_prepare": directories_to_prepare,
        },
        "cluster": {
            "ssh_config_host": NOTEBOOK_DEFAULTS["ssh_config_host"],
            "remote_host": NOTEBOOK_DEFAULTS["remote_host"],
            "username": resolved_username,
            "key_file": NOTEBOOK_DEFAULTS["key_file"],
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
            "python": resolved_remote_python,
            "script_name": "run_job.py",
            "submission_spec_name": SUBMISSION_SPEC_FILENAME,
            "backend_package_dir": REMOTE_BACKEND_PACKAGE_DIR,
            "backend_init_name": REMOTE_BACKEND_INIT_FILENAME,
            "execution_module_name": REMOTE_EXECUTION_MODULE_FILENAME,
            "stdout": log_out,
            "stderr": log_err,
        },
        "potcar": {
            "functional": potcar_functional,
            "species": potcar_species["species"],
            "symbols": potcar_species["symbols"],
            "symbol_source": potcar_species["source"],
            "repository": "shared" if use_shared_potcars else "private",
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
    "REMOTE_BACKEND_INIT_FILENAME",
    "REMOTE_BACKEND_MODULE_FILENAMES",
    "REMOTE_BACKEND_PACKAGE_DIR",
    "REMOTE_EXECUTION_MODULE_FILENAME",
    "SUBMISSION_SPEC_FILENAME",
    "build_backend_module_sources",
    "build_execution_module_source",
    "build_sbatch_script",
    "build_run_job_script",
    "build_submission_command",
    "create_submission_spec",
    "default_resources_for_calculation_spec",
    "default_resources_for_workflow",
    "parse_sbatch_job_id",
    "sanitize_label",
    "summarize_potcar_species",
]
