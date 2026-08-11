from __future__ import annotations

import gzip
import json
import logging
import posixpath
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from backend.calculations.models import CalculationSpec, WorkflowSpec
from backend.calculations.registry import (
    calculation_result_stage_directory,
    calculation_spec_from_flow_spec,
    workflow_result_stage_directory,
    workflow_spec_from_flow_spec,
)
from backend.config import DEFAULT_LOGS_DIR
from backend.remote import RemoteRunner
from backend.remote_runtime import (
    connected_remote_runner,
    connection_profile_from_submission_spec,
    default_connection_profile,
)
from backend.workflow_results import (
    render_workflow_results,
    workflow_result_file_keys,
    workflow_result_parse_dos,
    workflow_result_parse_eigenvalues,
)


RESULT_FILES = {
    "contcar": "CONTCAR",
    "outcar": "OUTCAR",
    "vasprun": "vasprun.xml",
    "doscar": "DOSCAR",
    "kpoints": "KPOINTS",
}
LOGGER = logging.getLogger(__name__)


def _log_results(message: str) -> None:
    LOGGER.debug(message)


def monitoring_indicates_success(monitoring_result: dict | None) -> bool:
    if not monitoring_result or monitoring_result.get("status") != "success":
        return False

    summary = str(monitoring_result.get("summary") or "").upper()
    state = str(monitoring_result.get("slurm_state") or "").upper()
    exit_code = monitoring_result.get("exit_code")

    if summary != "SUCCESS" or not state.startswith("COMPLETED"):
        return False

    return exit_code is None or str(exit_code).startswith("0:0")


def load_results_for_completed_job(
    monitoring_result: dict | None,
    *,
    submission_spec: dict | None = None,
    runner_factory: Callable[[], RemoteRunner] | None = None,
    parser: Callable[[dict, dict], dict] | None = None,
) -> dict | None:
    _log_results("ENTER results")
    _log_results("Check monitoring success")
    if not monitoring_indicates_success(monitoring_result):
        _log_results("Monitoring did not report SUCCESS")
        _log_results("RETURN results")
        return None
    _log_results("Monitoring reported SUCCESS")

    _log_results("Build results connection profile")
    profile = (
        connection_profile_from_submission_spec(submission_spec)
        if submission_spec is not None
        else default_connection_profile()
    )
    _log_results("Results connection profile built")

    try:
        _log_results("Connect results RemoteRunner")
        with connected_remote_runner(
            profile=profile,
            runner_factory=runner_factory,
        ) as runner:
            _log_results("Results RemoteRunner connected")
            _log_results("Locate run directory")
            location = resolve_results_location(
                runner,
                monitoring_result,
                submission_spec,
            )
            run_dir = location["run_dir"]
            if not run_dir:
                _log_results("Run directory missing")
                _log_results("RETURN results")
                return _failure_result(
                    "Results Discovery",
                    "Completed job has no canonical BMD run directory.",
                    "Check that BMD Compute wrote the remote job state record for this SLURM job.",
                )
            _log_results("Run directory found")

            _log_results("Build direct result paths")
            output_dir = location["output_dir"]
            paths = result_file_paths(
                output_dir,
                extra_file_keys=location["workflow_result_file_keys"],
            )
            _log_results("Direct result paths built")
            _log_results("Verify result files")
            missing = missing_result_files(runner, paths)
            _log_results("Result file verification complete")
            if missing:
                _log_results("Required result file missing")
                _log_results("RETURN results")
                return _failure_result(
                    "Results Discovery",
                    f"Completed calculation is missing required output files: {', '.join(missing)}.",
                    "Check the BMD run directory and confirm VASP wrote CONTCAR, OUTCAR, and vasprun.xml.",
                    files=paths,
                )

            _log_results("Read result files")
            files = read_result_files(runner, paths)
            _log_results("Result file reads complete")
    except Exception as exc:
        _log_results("Results retrieval failed")
        _log_results("RETURN results")
        return _failure_result(
            "Results Retrieval",
            _clean_message(exc),
            "Check remote file permissions and try refreshing monitoring.",
            exception=exc,
        )

    parse = parser or parse_vasp_result_files
    parse_context = _results_parse_context(monitoring_result, location)
    try:
        _log_results("Parse result files")
        result = parse(files, parse_context)
        _log_results("Result file parsing complete")
    except Exception as exc:
        _log_results("Results parsing failed")
        _log_results("RETURN results")
        return _failure_result(
            "Results Parsing",
            _clean_message(exc),
            "Check that pymatgen is installed and that the VASP output files are complete.",
            files={key: value["path"] for key, value in files.items()},
            exception=exc,
        )

    result.setdefault("status", "success")
    result.setdefault("title", "Results Summary")
    result.setdefault("run_dir", run_dir)
    result.setdefault("workdir", output_dir)
    result.setdefault("job_id", monitoring_result.get("job_id"))
    result.setdefault("files", {key: value["path"] for key, value in files.items()})
    result.setdefault("visualizations", [])
    _log_results("RETURN results")
    return result


def resolve_results_location(
    runner: RemoteRunner,
    monitoring_result: dict,
    submission_spec: dict | None,
) -> dict:
    state = {}
    resolved_spec = submission_spec

    if resolved_spec is None:
        job_id = str(monitoring_result.get("job_id") or "").strip()
        if not job_id:
            return _empty_results_location()

        state_path = remote_job_state_path(job_id)
        _log_results("Locate BMD remote job state")
        if not runner.is_file(state_path):
            _log_results("BMD remote job state missing")
            return _empty_results_location()
        _log_results("BMD remote job state found")

        _log_results("Read BMD remote job state")
        payload = runner.read_text(state_path)
        _log_results("BMD remote job state read")
        state = json.loads(payload)
        resolved_spec = state.get("submission_spec")

    run_dir = _run_dir_from_submission_or_state(resolved_spec, state)
    if not run_dir:
        return _empty_results_location()

    workflow_spec = workflow_spec_from_submission_spec(resolved_spec)
    calculation_spec = calculation_spec_from_submission_spec(resolved_spec)
    output_dir = result_output_dir_from_submission_spec(run_dir, resolved_spec)
    return {
        "run_dir": run_dir,
        "output_dir": output_dir,
        "calculation_spec": calculation_spec,
        "workflow_spec": workflow_spec,
        "workflow_result_file_keys": workflow_result_file_keys(
            workflow_spec or calculation_spec
        ),
    }


def resolve_results_run_dir(
    runner: RemoteRunner,
    monitoring_result: dict,
    submission_spec: dict | None,
) -> str:
    return resolve_results_location(
        runner,
        monitoring_result,
        submission_spec,
    )["run_dir"]


def _run_dir_from_submission_or_state(
    submission_spec: dict | None,
    state: dict,
) -> str:
    if submission_spec is not None:
        run_dir = submission_spec.get("paths", {}).get("run_dir")
        if run_dir:
            return str(run_dir)

    return str(state.get("run_dir") or "")


def result_stage_directory_from_submission_spec(submission_spec: dict | None) -> str | None:
    if not submission_spec:
        return None

    workflow_spec = workflow_spec_from_submission_spec(submission_spec)
    if workflow_spec is not None:
        return workflow_result_stage_directory(workflow_spec)

    calculation_spec = calculation_spec_from_submission_spec(submission_spec)
    if calculation_spec is None:
        return None

    return calculation_result_stage_directory(calculation_spec)


def calculation_spec_from_submission_spec(
    submission_spec: dict | None,
) -> CalculationSpec | None:
    if not submission_spec:
        return None

    try:
        return calculation_spec_from_flow_spec(
            submission_spec.get("flow_spec")
        )
    except Exception:
        return None


def workflow_spec_from_submission_spec(
    submission_spec: dict | None,
) -> WorkflowSpec | None:
    if not submission_spec:
        return None

    try:
        return workflow_spec_from_flow_spec(
            submission_spec.get("flow_spec")
        )
    except Exception:
        return None


def result_includes_dos_from_submission_spec(submission_spec: dict | None) -> bool:
    workflow_spec = workflow_spec_from_submission_spec(submission_spec)
    calculation_spec = calculation_spec_from_submission_spec(submission_spec)
    return "doscar" in workflow_result_file_keys(workflow_spec or calculation_spec)


def _empty_results_location() -> dict:
    return {
        "run_dir": "",
        "output_dir": "",
        "calculation_spec": None,
        "workflow_spec": None,
        "workflow_result_file_keys": (),
    }


def result_output_dir_from_submission_spec(
    run_dir: str,
    submission_spec: dict | None,
) -> str:
    if submission_spec:
        result_dir = submission_spec.get("paths", {}).get("result_dir")
        if result_dir:
            return str(result_dir)

    stage_dir = result_stage_directory_from_submission_spec(submission_spec)
    if stage_dir:
        return posixpath.join(run_dir.rstrip("/"), stage_dir)

    return run_dir


def remote_job_state_path(job_id: str) -> str:
    safe_job_id = str(job_id or "").strip().split(".", 1)[0]
    return posixpath.join(DEFAULT_LOGS_DIR, f"job_{safe_job_id}.json")


def result_file_paths(
    run_dir: str,
    *,
    include_dos: bool = False,
    extra_file_keys: tuple[str, ...] = (),
) -> dict[str, str]:
    root = run_dir.rstrip("/")
    keys = ["contcar", "outcar", "vasprun"]
    if include_dos:
        keys.append("doscar")
    for key in extra_file_keys:
        if key not in keys:
            keys.append(key)
    return {
        key: posixpath.join(root, filename)
        for key, filename in RESULT_FILES.items()
        if key in keys
    }


def missing_result_files(runner: RemoteRunner, paths: dict[str, str]) -> list[str]:
    missing = []
    for key, remote_path in paths.items():
        _log_results(f"Verify {RESULT_FILES[key]}")
        if not runner.is_file(remote_path):
            missing.append(RESULT_FILES[key])
            _log_results(f"{RESULT_FILES[key]} missing")
        else:
            _log_results(f"{RESULT_FILES[key]} found")
    return missing


def read_result_files(runner: RemoteRunner, paths: dict[str, str | None]) -> dict:
    files = {}
    for key, remote_path in paths.items():
        if not remote_path:
            continue
        _log_results(f"Read {RESULT_FILES[key]}")
        data = runner.read_bytes(remote_path)
        _log_results(f"{RESULT_FILES[key]} read complete")
        _log_results(f"Decode {RESULT_FILES[key]}")
        files[key] = {
            "path": remote_path,
            "text": _decode_remote_output(remote_path, data),
        }
        _log_results(f"{RESULT_FILES[key]} decode complete")
    return files


def parse_vasp_result_files(files: dict, monitoring_result: dict) -> dict:
    _log_results("ENTER parse_vasp_result_files")
    from pymatgen.core import Structure
    from pymatgen.io.vasp.outputs import Outcar, Vasprun

    with tempfile.TemporaryDirectory(prefix="bmd-results-") as tmpdir:
        _log_results("Write temporary VASP files")
        tmp = Path(tmpdir)
        workflow_spec = _workflow_spec_from_results_context(monitoring_result)
        calculation_spec = _calculation_spec_from_results_context(monitoring_result)
        result_spec = workflow_spec or calculation_spec
        parse_dos = workflow_result_parse_dos(
            result_spec,
            available_file_keys=set(files),
        )
        parse_eigenvalues = workflow_result_parse_eigenvalues(
            result_spec,
            available_file_keys=set(files),
        )
        local_paths = {
            "contcar": tmp / "CONTCAR",
            "outcar": tmp / "OUTCAR",
            "vasprun": tmp / "vasprun.xml",
        }
        if "doscar" in files:
            local_paths["doscar"] = tmp / "DOSCAR"
        if "kpoints" in files:
            local_paths["kpoints"] = tmp / "KPOINTS"

        for key, local_path in local_paths.items():
            local_path.write_text(files[key]["text"], encoding="utf-8")
        _log_results("Temporary VASP files written")

        _log_results("Parse Structure")
        contcar_structure = Structure.from_file(str(local_paths["contcar"]))
        _log_results("Structure parsed")

        outcar_parsed = True
        outcar_error = ""
        try:
            _log_results("Parse Outcar")
            Outcar(str(local_paths["outcar"]))
            _log_results("Outcar parsed")
        except Exception as exc:
            outcar_parsed = False
            outcar_error = str(exc)
            _log_results("Outcar parse failed")

        try:
            _log_results("Parse Vasprun")
            vasprun = Vasprun(
                str(local_paths["vasprun"]),
                **_vasprun_parse_kwargs(
                    parse_dos=parse_dos,
                    parse_eigenvalues=parse_eigenvalues,
                ),
            )
            _log_results("Vasprun parsed")
        except TypeError:
            _log_results("Parse Vasprun with legacy eigenvalue argument")
            vasprun = Vasprun(
                str(local_paths["vasprun"]),
                **_vasprun_parse_kwargs(
                    parse_dos=parse_dos,
                    parse_eigenvalues=parse_eigenvalues,
                    legacy_eigen_arg=True,
                ),
            )
            _log_results("Vasprun parsed")

        _log_results("Extract final structure")
        final_structure = getattr(vasprun, "final_structure", None) or contcar_structure
        _log_results("Final structure extracted")
        _log_results("Extract final energy")
        final_energy = _float_or_none(getattr(vasprun, "final_energy", None))
        _log_results("Final energy extracted")
        natoms = len(final_structure)
        energy_per_atom = (
            _round_float(final_energy / natoms)
            if final_energy is not None and natoms
            else None
        )
        electronic_convergence = getattr(vasprun, "converged_electronic", None)
        if electronic_convergence is None:
            electronic_convergence = getattr(vasprun, "converged", None)

        completion_status = _completion_status(monitoring_result)
        formula = final_structure.composition.reduced_formula
        workflow_payload = render_workflow_results(
            result_spec,
            vasprun=vasprun,
            files=files,
            local_paths=local_paths,
        )
        _log_results("Generate CIF")
        cif_text = final_structure.to(fmt="cif")
        _log_results("CIF generated")

        _log_results("RETURN parse_vasp_result_files")
        result = {
            "status": "success",
            "title": "Results Summary",
            "completion_status": completion_status,
            "final_energy_ev": _round_float(final_energy),
            "energy_per_atom_ev": energy_per_atom,
            "ionic_steps": len(getattr(vasprun, "ionic_steps", []) or []),
            "electronic_convergence": _bool_or_none(electronic_convergence),
            "final_formula": formula,
            "natoms": natoms,
            "files": {key: value["path"] for key, value in files.items()},
            "diagnostics": {
                "outcar_parsed": outcar_parsed,
                "outcar_error": outcar_error,
                "parser": "pymatgen",
            },
            "visualizations": workflow_payload.visualizations,
            "viewer": {
                "format": "cif",
                "source": "CONTCAR",
                "cif": cif_text,
            },
        }
        for key, summary in workflow_payload.summaries.items():
            result[key] = summary
        return result


def _decode_remote_output(remote_path: str, data: bytes) -> str:
    raw = data or b""
    if remote_path.endswith(".gz") or raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def _completion_status(monitoring_result: dict) -> str:
    state = monitoring_result.get("slurm_state") or "COMPLETED"
    exit_code = monitoring_result.get("exit_code") or "0:0"
    return f"{state} (ExitCode {exit_code})"


def _vasprun_parse_kwargs(
    *,
    parse_dos: bool,
    parse_eigenvalues: bool,
    legacy_eigen_arg: bool = False,
) -> dict:
    common = {
        "exception_on_bad_xml": False,
        "parse_potcar_file": False,
    }
    if parse_eigenvalues:
        # Keep pymatgen's standard band-structure parse path; parse_dos=False can leave efermi unset.
        return common

    eigen_key = "parse_eigen" if legacy_eigen_arg else "parse_eigenvalues"
    return {
        "parse_dos": parse_dos,
        eigen_key: False,
        **common,
    }


def _results_parse_context(monitoring_result: dict | None, location: dict) -> dict:
    context = dict(monitoring_result or {})
    workflow_spec = location.get("workflow_spec")
    if workflow_spec is not None:
        context["workflow_spec"] = workflow_spec.to_dict()
    calculation_spec = location.get("calculation_spec")
    if calculation_spec is not None:
        context["calculation_spec"] = calculation_spec.to_dict()
    return context


def _workflow_spec_from_results_context(context: dict | None) -> WorkflowSpec | None:
    if not context:
        return None

    value = context.get("workflow_spec")
    if isinstance(value, WorkflowSpec):
        return value
    if value:
        try:
            return WorkflowSpec.from_dict(value)
        except Exception:
            return None

    return workflow_spec_from_submission_spec(context.get("submission_spec"))


def _calculation_spec_from_results_context(context: dict | None) -> CalculationSpec | None:
    if not context:
        return None

    value = context.get("calculation_spec")
    if isinstance(value, CalculationSpec):
        return value
    if value:
        try:
            return CalculationSpec.from_dict(value)
        except Exception:
            return None

    return calculation_spec_from_submission_spec(context.get("submission_spec"))


def _failure_result(
    stage: str,
    reason: str,
    suggestion: str,
    *,
    files: dict | None = None,
    exception: Exception | None = None,
) -> dict:
    result = {
        "status": "failed",
        "title": "Results Summary Failed",
        "stage": stage,
        "reason": reason,
        "suggestion": suggestion,
        "files": files or {},
    }
    if exception is not None:
        result["exception_text"] = str(exception)
        result["traceback"] = "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )
    return result


def _clean_message(value) -> str:
    text = str(value or "").strip()
    return text or "Results processing failed."


def _round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _bool_or_none(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


__all__ = [
    "load_results_for_completed_job",
    "missing_result_files",
    "monitoring_indicates_success",
    "parse_vasp_result_files",
    "read_result_files",
    "remote_job_state_path",
    "resolve_results_location",
    "resolve_results_run_dir",
    "result_includes_dos_from_submission_spec",
    "result_output_dir_from_submission_spec",
    "result_stage_directory_from_submission_spec",
    "result_file_paths",
]
