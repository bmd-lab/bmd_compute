from __future__ import annotations

import os
import time
import traceback
from typing import Any


BAND_STRUCTURE_INITIAL_YAXIS_RANGE = [-10, 10]


def parse_result_paths(
    paths: dict[str, str],
    context: dict,
    *,
    display_paths: dict[str, str] | None = None,
    parser_name: str = "pymatgen",
    include_remote_metadata: bool = False,
) -> dict:
    """
    Parse completed VASP result files from paths that already exist locally.

    This module is intentionally self-contained so the same source can be sent
    to PowerSLURM and executed beside the result files without importing the
    BMD web application package.
    """

    started = time.time()

    from pymatgen.core import Structure
    from pymatgen.io.vasp.outputs import Outcar, Vasprun

    shown_paths = display_paths or paths
    files = {
        key: {
            "path": value,
        }
        for key, value in shown_paths.items()
        if value
    }
    available_file_keys = set(files)
    result_spec = _result_spec_from_context(context)
    parse_dos = _workflow_result_parse_dos(
        result_spec,
        available_file_keys=available_file_keys,
    )
    parse_eigenvalues = _workflow_result_parse_eigenvalues(
        result_spec,
        available_file_keys=available_file_keys,
    )

    contcar_structure = Structure.from_file(str(paths["contcar"]))

    outcar_parsed = True
    outcar_error = ""
    try:
        Outcar(str(paths["outcar"]))
    except Exception as exc:
        outcar_parsed = False
        outcar_error = str(exc)

    try:
        vasprun = Vasprun(
            str(paths["vasprun"]),
            **_vasprun_parse_kwargs(
                parse_dos=parse_dos,
                parse_eigenvalues=parse_eigenvalues,
            ),
        )
    except TypeError:
        vasprun = Vasprun(
            str(paths["vasprun"]),
            **_vasprun_parse_kwargs(
                parse_dos=parse_dos,
                parse_eigenvalues=parse_eigenvalues,
                legacy_eigen_arg=True,
            ),
        )

    final_structure = getattr(vasprun, "final_structure", None) or contcar_structure
    final_energy = _float_or_none(getattr(vasprun, "final_energy", None))
    natoms = len(final_structure)
    energy_per_atom = (
        _round_float(final_energy / natoms)
        if final_energy is not None and natoms
        else None
    )
    electronic_convergence = getattr(vasprun, "converged_electronic", None)
    if electronic_convergence is None:
        electronic_convergence = getattr(vasprun, "converged", None)
    ionic_convergence = None
    if _result_has_final_relaxation_stage(result_spec):
        ionic_convergence = getattr(vasprun, "converged_ionic", None)

    workflow_payload = _render_workflow_results(
        result_spec,
        vasprun=vasprun,
        files=files,
        local_paths=paths,
    )
    final_formula = final_structure.composition.reduced_formula
    cif_text = final_structure.to(fmt="cif")

    diagnostics = {
        "outcar_parsed": outcar_parsed,
        "outcar_error": outcar_error,
        "parser": parser_name,
    }
    if include_remote_metadata:
        diagnostics["remote_parsing"] = {
            "source_file_bytes": _source_file_bytes(paths),
            "source_bytes": _source_bytes(paths),
            "remote_parse_elapsed_s": _round_float(time.time() - started),
        }

    result = {
        "status": "success",
        "title": "Results Summary",
        "completion_status": _completion_status(context),
        "final_energy_ev": _round_float(final_energy),
        "energy_per_atom_ev": energy_per_atom,
        "ionic_steps": len(getattr(vasprun, "ionic_steps", []) or []),
        "electronic_convergence": _bool_or_none(electronic_convergence),
        "converged_electronic": _bool_or_none(electronic_convergence),
        "ionic_convergence": _bool_or_none(ionic_convergence),
        "converged_ionic": _bool_or_none(ionic_convergence),
        "final_formula": final_formula,
        "natoms": natoms,
        "files": {key: value["path"] for key, value in files.items()},
        "diagnostics": diagnostics,
        "visualizations": workflow_payload["visualizations"],
        "viewer": {
            "format": "cif",
            "source": "CONTCAR",
            "cif": cif_text,
        },
    }
    result.update(workflow_payload["summaries"])
    return result


def remote_failure_result(paths: dict[str, str], exc: Exception) -> dict:
    return {
        "status": "failed",
        "title": "Results Summary Failed",
        "stage": "Results Parsing",
        "reason": str(exc).strip() or "Remote pymatgen result parsing failed.",
        "suggestion": "Check that the remote VASP output files are complete and readable.",
        "files": dict(paths),
        "exception_text": str(exc),
        "traceback": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
        "diagnostics": {
            "parser": "pymatgen-remote",
            "remote_parsing": {
                "source_file_bytes": _source_file_bytes(paths),
                "source_bytes": _source_bytes(paths),
            },
        },
    }


def _result_spec_from_context(context: dict | None) -> dict | None:
    if not context:
        return None
    return context.get("workflow_spec") or context.get("calculation_spec")


def _workflow_stage_types(spec: dict | None) -> tuple[str, ...]:
    if not isinstance(spec, dict):
        return ()

    stages = spec.get("stages")
    if isinstance(stages, list):
        return tuple(
            str(stage.get("stage_type") or "").strip().lower()
            for stage in stages
            if isinstance(stage, dict)
        )

    purpose = str(spec.get("purpose") or "").strip().lower()
    if purpose == "double_relax":
        return ("relax", "relax")
    if purpose == "relax_static":
        return ("relax", "static")
    if purpose == "dos":
        return ("relax", "static", "dos")
    if purpose == "band_structure":
        return ("relax", "static", "band_structure")
    return (purpose,) if purpose else ()


def _workflow_result_parse_dos(
    spec: dict | None,
    *,
    available_file_keys: set[str] | None = None,
) -> bool:
    stage_types = _workflow_stage_types(spec)
    return "dos" in stage_types or bool(
        available_file_keys and "doscar" in available_file_keys
    )


def _workflow_result_parse_eigenvalues(
    spec: dict | None,
    *,
    available_file_keys: set[str] | None = None,
) -> bool:
    stage_types = _workflow_stage_types(spec)
    return "band_structure" in stage_types or bool(
        available_file_keys and "kpoints" in available_file_keys
    )


def _render_workflow_results(
    spec: dict | None,
    *,
    vasprun,
    files: dict,
    local_paths: dict,
) -> dict:
    summaries = {}
    visualizations = []
    stage_types = _workflow_stage_types(spec)
    available_file_keys = set(files)

    if "dos" in stage_types or "doscar" in available_file_keys:
        summary, visualization = _dos_result_payload(vasprun, files)
        summaries["dos"] = summary
        if visualization is not None:
            visualizations.append(visualization)

    if "band_structure" in stage_types or "kpoints" in available_file_keys:
        summary, visualization = _band_structure_result_payload(
            vasprun,
            files,
            local_paths,
        )
        summaries["band_structure"] = summary
        if visualization is not None:
            visualizations.append(visualization)

    return {
        "summaries": summaries,
        "visualizations": visualizations,
    }


def _dos_result_payload(vasprun, files: dict) -> tuple[dict, dict | None]:
    total_dos = _total_dos_from_vasprun(vasprun)
    energies = _as_float_list(getattr(total_dos, "energies", None))
    densities = getattr(total_dos, "densities", None) or {}
    efermi = _float_or_none(
        getattr(total_dos, "efermi", None)
        if total_dos is not None
        else None
    )
    if efermi is None:
        efermi = _float_or_none(getattr(vasprun, "efermi", None))

    summary = {
        "available": total_dos is not None,
        "source": "vasprun.xml",
        "doscar": files.get("doscar", {}).get("path"),
        "energy_points": len(energies) if energies else None,
        "energy_min_ev": _round_float(min(energies)) if energies else None,
        "energy_max_ev": _round_float(max(energies)) if energies else None,
        "spin_channels": len(densities) if densities else None,
        "fermi_level_ev": _round_float(efermi),
    }
    if total_dos is None or not energies or not densities:
        return summary, None

    x_values = [
        _round_float(energy - efermi) if efermi is not None else _round_float(energy)
        for energy in energies
    ]
    traces = []
    for spin, values in densities.items():
        y_values = _as_float_list(values)
        if not y_values:
            continue
        multiplier = -1.0 if _is_spin_down(spin) else 1.0
        traces.append(
            {
                "name": _spin_label(spin),
                "y": [_round_float(multiplier * value) for value in y_values],
            }
        )

    if not traces:
        return summary, None

    visualization = {
        "id": "dos",
        "element_id": "workflow-visualization-dos",
        "kind": "line_plot",
        "title": "Density of States",
        "subtitle": "Total DOS from vasprun.xml",
        "download_filename": "density_of_states.png",
        "plot": {
            "x": x_values,
            "traces": traces,
            "xaxis_title": "Energy - E_F (eV)" if efermi is not None else "Energy (eV)",
            "yaxis_title": "Density of States",
            "x_hover_unit": " eV",
            "y_hover_unit": "",
            "energy_reference": "fermi" if efermi is not None else "absolute",
            "reference_axis": "x" if efermi is not None else None,
            "fermi_level_ev": _round_float(efermi),
            "source": "vasprun.xml",
        },
    }
    return summary, visualization


def _band_structure_result_payload(
    vasprun,
    files: dict,
    local_paths: dict,
) -> tuple[dict, dict | None]:
    band_structure = _band_structure_from_vasprun(vasprun, local_paths)
    bands = getattr(band_structure, "bands", None) or {}
    kpoint_count = _band_kpoint_count(band_structure, bands)
    band_count = _band_count(bands)
    efermi = _float_or_none(getattr(band_structure, "efermi", None))
    gap = _band_gap_summary(band_structure)

    summary = {
        "available": band_structure is not None and bool(bands),
        "source": "vasprun.xml",
        "kpoints": files.get("kpoints", {}).get("path"),
        "bands": band_count,
        "kpoints_count": kpoint_count,
        "spin_channels": len(bands) if bands else None,
        "fermi_level_ev": _round_float(efermi),
        "band_gap_ev": gap["band_gap_ev"],
        "is_metal": gap["is_metal"],
        "direct_gap": gap["direct_gap"],
    }
    if band_structure is None or not bands or not kpoint_count:
        return summary, None

    x_values = _band_distances(band_structure, kpoint_count)
    ticks = _band_ticks(band_structure, x_values)
    traces = []
    spin_polarized = len(bands) > 1
    for spin, matrix in bands.items():
        spin_label = _spin_label(spin) if spin_polarized else "Bands"
        spin_down = _is_spin_down(spin)
        rows = _matrix_rows(matrix)
        for band_index, row in enumerate(rows, start=1):
            if len(row) != len(x_values):
                continue
            if efermi is not None:
                y_values = [_round_float(value - efermi) for value in row]
            else:
                y_values = [_round_float(value) for value in row]
            traces.append(
                {
                    "name": spin_label,
                    "legendgroup": spin_label,
                    "showlegend": spin_polarized and band_index == 1,
                    "color": "#c94001" if spin_down else "#1a6aff",
                    "dash": "dash" if spin_down else "solid",
                    "y": y_values,
                }
            )

    if not traces:
        return summary, None

    visualization = {
        "id": "band_structure",
        "element_id": "workflow-visualization-band-structure",
        "kind": "line_plot",
        "title": "Band Structure",
        "subtitle": "Line-mode band path from vasprun.xml",
        "download_filename": "band_structure.png",
        "plot": {
            "x": x_values,
            "traces": traces,
            "xaxis_title": "K-point path",
            "yaxis_title": "Energy - E_F (eV)" if efermi is not None else "Energy (eV)",
            "yaxis_range": BAND_STRUCTURE_INITIAL_YAXIS_RANGE,
            "x_hover_unit": "",
            "y_hover_unit": " eV",
            "tickvals": ticks["tickvals"],
            "ticktext": ticks["ticktext"],
            "vertical_lines": ticks["tickvals"],
            "energy_reference": "fermi" if efermi is not None else "absolute",
            "reference_axis": "y" if efermi is not None else None,
            "fermi_level_ev": _round_float(efermi),
            "source": "vasprun.xml",
        },
    }
    return summary, visualization


def _total_dos_from_vasprun(vasprun):
    complete_dos = getattr(vasprun, "complete_dos", None)
    return getattr(vasprun, "tdos", None) or getattr(complete_dos, "tdos", None)


def _band_structure_from_vasprun(vasprun, local_paths: dict):
    existing = getattr(vasprun, "band_structure", None)
    if existing is not None:
        return existing

    get_band_structure = getattr(vasprun, "get_band_structure", None)
    if not callable(get_band_structure):
        return None

    kpoints_path = local_paths.get("kpoints")
    if kpoints_path is not None:
        return get_band_structure(
            kpoints_filename=str(kpoints_path),
            line_mode=True,
        )

    return get_band_structure(line_mode=True)


def _result_has_final_relaxation_stage(result_spec: dict | None) -> bool:
    stage_types = _workflow_stage_types(result_spec)
    return bool(stage_types and stage_types[-1] == "relax")


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
        return common

    eigen_key = "parse_eigen" if legacy_eigen_arg else "parse_eigenvalues"
    return {
        "parse_dos": parse_dos,
        eigen_key: False,
        **common,
    }


def _completion_status(context: dict) -> str:
    state = context.get("slurm_state") or "COMPLETED"
    exit_code = context.get("exit_code") or "0:0"
    return f"{state} (ExitCode {exit_code})"


def _source_file_bytes(paths: dict[str, str]) -> dict[str, int | None]:
    sizes = {}
    for key, path in paths.items():
        try:
            sizes[key] = os.path.getsize(path)
        except OSError:
            sizes[key] = None
    return sizes


def _source_bytes(paths: dict[str, str]) -> int:
    return sum(
        size
        for size in _source_file_bytes(paths).values()
        if isinstance(size, int)
    )


def _as_float_list(values: Any) -> list[float]:
    if values is None:
        return []

    try:
        iterable = list(values)
    except TypeError:
        return []

    converted = []
    for value in iterable:
        numeric = _float_or_none(value)
        if numeric is not None:
            converted.append(numeric)

    return converted


def _matrix_rows(values: Any) -> list[list[float]]:
    if values is None:
        return []

    if hasattr(values, "tolist"):
        values = values.tolist()

    try:
        rows = list(values)
    except TypeError:
        return []

    converted = []
    for row in rows:
        if hasattr(row, "tolist"):
            row = row.tolist()
        converted_row = _as_float_list(row)
        if converted_row:
            converted.append(converted_row)

    return converted


def _band_kpoint_count(band_structure, bands: dict) -> int | None:
    kpoints = getattr(band_structure, "kpoints", None)
    if kpoints:
        return len(kpoints)

    for matrix in bands.values():
        rows = _matrix_rows(matrix)
        if rows:
            return len(rows[0])

    return None


def _band_count(bands: dict) -> int | None:
    counts = [
        len(_matrix_rows(matrix))
        for matrix in bands.values()
    ]
    return max(counts) if counts else None


def _band_distances(band_structure, kpoint_count: int) -> list[float]:
    distances = _as_float_list(getattr(band_structure, "distance", None))
    if len(distances) == kpoint_count:
        return [_round_float(value) for value in distances]

    return [float(index) for index in range(kpoint_count)]


def _band_ticks(band_structure, x_values: list[float]) -> dict:
    tickvals: list[float] = []
    ticktext: list[str] = []
    kpoints = getattr(band_structure, "kpoints", None) or []

    for index, kpoint in enumerate(kpoints):
        if index >= len(x_values):
            break
        label = _format_kpoint_label(getattr(kpoint, "label", None))
        if not label:
            continue
        value = x_values[index]
        if tickvals and abs(tickvals[-1] - value) < 1e-8:
            labels = ticktext[-1].split("|")
            if label not in labels:
                ticktext[-1] = ticktext[-1] + "|" + label
            continue
        tickvals.append(value)
        ticktext.append(label)

    return {
        "tickvals": tickvals,
        "ticktext": ticktext,
    }


def _band_gap_summary(band_structure) -> dict:
    if band_structure is None:
        return {
            "band_gap_ev": None,
            "is_metal": None,
            "direct_gap": None,
        }

    band_gap = None
    direct_gap = None
    try:
        gap = band_structure.get_band_gap()
        band_gap = _round_float(_float_or_none(gap.get("energy")))
        direct_gap = bool(gap.get("direct")) if gap.get("direct") is not None else None
    except Exception:
        pass

    is_metal = None
    is_metal_fn = getattr(band_structure, "is_metal", None)
    if callable(is_metal_fn):
        try:
            is_metal = bool(is_metal_fn())
        except Exception:
            is_metal = None

    return {
        "band_gap_ev": band_gap,
        "is_metal": is_metal,
        "direct_gap": direct_gap,
    }


def _format_kpoint_label(label) -> str:
    text = str(label or "").strip()
    if not text:
        return ""

    return "|".join(
        _format_single_kpoint_label(part)
        for part in text.split("|")
    )


def _format_single_kpoint_label(label: str) -> str:
    text = label.strip().replace("$", "")
    gamma_tokens = {
        "\\Gamma",
        "\\gamma",
        "Gamma",
        "GAMMA",
        "gamma",
        "Γ",
    }
    return "Γ" if text in gamma_tokens else text


def _spin_label(spin) -> str:
    name = str(getattr(spin, "name", "") or "").strip().lower()
    text = str(spin or "").strip().lower()
    value = getattr(spin, "value", None)

    if value == -1 or name == "down" or "down" in text:
        return "Spin down"
    if value == 1 or name == "up" or "up" in text:
        return "Spin up"

    return str(spin)


def _is_spin_down(spin) -> bool:
    name = str(getattr(spin, "name", "") or "").strip().lower()
    text = str(spin or "").strip().lower()
    return getattr(spin, "value", None) == -1 or name == "down" or "down" in text


def _round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value) -> bool | None:
    if value is None:
        return None
    return bool(value)
