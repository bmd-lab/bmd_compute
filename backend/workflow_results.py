from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.calculations.models import CalculationSpec, Purpose


@dataclass(frozen=True)
class WorkflowResultPayload:
    summaries: dict[str, dict]
    visualizations: list[dict]


class WorkflowResultRenderer:
    purpose: Purpose
    required_file_keys: tuple[str, ...] = ()
    parse_dos: bool = False

    def render(self, *, vasprun, files: dict) -> WorkflowResultPayload:
        raise NotImplementedError


class DensityOfStatesResultRenderer(WorkflowResultRenderer):
    purpose = Purpose.DOS
    required_file_keys = ("doscar",)
    parse_dos = True

    def render(self, *, vasprun, files: dict) -> WorkflowResultPayload:
        summary, visualization = _dos_result_payload(vasprun, files)
        visualizations = [visualization] if visualization is not None else []
        return WorkflowResultPayload(
            summaries={"dos": summary},
            visualizations=visualizations,
        )


_WORKFLOW_RESULT_RENDERERS: dict[Purpose, WorkflowResultRenderer] = {
    Purpose.DOS: DensityOfStatesResultRenderer(),
}


def workflow_result_renderers(
    spec: CalculationSpec | None,
    *,
    available_file_keys: set[str] | None = None,
) -> tuple[WorkflowResultRenderer, ...]:
    if spec is not None:
        renderer = _WORKFLOW_RESULT_RENDERERS.get(spec.purpose)
        return (renderer,) if renderer is not None else ()

    if available_file_keys and "doscar" in available_file_keys:
        return (_WORKFLOW_RESULT_RENDERERS[Purpose.DOS],)

    return ()


def workflow_result_file_keys(spec: CalculationSpec | None) -> tuple[str, ...]:
    keys: list[str] = []
    for renderer in workflow_result_renderers(spec):
        for key in renderer.required_file_keys:
            if key not in keys:
                keys.append(key)

    return tuple(keys)


def workflow_result_parse_dos(
    spec: CalculationSpec | None,
    *,
    available_file_keys: set[str] | None = None,
) -> bool:
    return any(
        renderer.parse_dos
        for renderer in workflow_result_renderers(
            spec,
            available_file_keys=available_file_keys,
        )
    )


def render_workflow_results(
    spec: CalculationSpec | None,
    *,
    vasprun,
    files: dict,
) -> WorkflowResultPayload:
    summaries: dict[str, dict] = {}
    visualizations: list[dict] = []
    renderers = workflow_result_renderers(
        spec,
        available_file_keys=set(files),
    )

    for renderer in renderers:
        payload = renderer.render(vasprun=vasprun, files=files)
        summaries.update(payload.summaries)
        visualizations.extend(payload.visualizations)

    return WorkflowResultPayload(
        summaries=summaries,
        visualizations=visualizations,
    )


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
        "plot": {
            "x": x_values,
            "traces": traces,
            "xaxis_title": "Energy - E_F (eV)" if efermi is not None else "Energy (eV)",
            "yaxis_title": "Density of States",
            "energy_reference": "fermi" if efermi is not None else "absolute",
            "fermi_level_ev": _round_float(efermi),
            "source": "vasprun.xml",
        },
    }
    return summary, visualization


def _total_dos_from_vasprun(vasprun):
    complete_dos = getattr(vasprun, "complete_dos", None)
    return getattr(vasprun, "tdos", None) or getattr(complete_dos, "tdos", None)


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


__all__ = [
    "DensityOfStatesResultRenderer",
    "WorkflowResultPayload",
    "WorkflowResultRenderer",
    "render_workflow_results",
    "workflow_result_file_keys",
    "workflow_result_parse_dos",
    "workflow_result_renderers",
]
