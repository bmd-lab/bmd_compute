from backend.calculations.models import CalculationSpec, Modifier, Purpose
from backend.calculations.registry import (
    calculation_display_name,
    calculation_spec_from_legacy,
)


def workflow_label_from_spec(spec: CalculationSpec) -> str:
    """
    Return the user-facing calculation label for a calculation intent.

    The labels branch on scientific intent rather than legacy workflow strings.
    """

    if spec.purpose is Purpose.RELAX and Modifier.IONS_ONLY in spec.modifiers:
        return "Geometry Optimisation"

    return calculation_display_name(spec)


def calculation_plan_from_spec(spec: CalculationSpec) -> list[str]:
    if spec.purpose is Purpose.DOS:
        return [
            "Geometry Optimisation",
            "Static Energy",
            "Density of States",
        ]

    if spec.purpose is Purpose.BAND_STRUCTURE:
        return [
            "Geometry Optimisation",
            "Static Energy",
            "Band Structure",
        ]

    if spec.purpose is Purpose.DOUBLE_RELAX:
        return [
            "Geometry Optimisation",
            "Geometry Optimisation",
        ]

    return [workflow_label_from_spec(spec)]


def _coerce_calculation_spec(calculation) -> CalculationSpec:
    if isinstance(calculation, CalculationSpec):
        return calculation

    return calculation_spec_from_legacy(calculation)


def summarize_workflow(flow, calculation):
    """
    Return a template-friendly summary of a Jobflow Flow.
    """

    spec = _coerce_calculation_spec(calculation)
    jobs = list(getattr(flow, "jobs", []) or [])

    return {
        "calculation_type": workflow_label_from_spec(spec),
        "workflow_type": workflow_label_from_spec(spec),
        "flow_name": getattr(flow, "name", "Unknown"),
        "number_of_jobs": len(jobs),
        "job_names": [getattr(job, "name", "Unknown") for job in jobs],
        "calculation_plan": calculation_plan_from_spec(spec),
        "ready_for_submission": True,
    }


__all__ = [
    "summarize_workflow",
    "calculation_plan_from_spec",
    "workflow_label_from_spec",
]
