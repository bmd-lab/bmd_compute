from backend.calculations.models import CalculationSpec, Modifier, Purpose, WorkflowSpec
from backend.calculations.registry import (
    calculation_spec_from_workflow_spec,
    calculation_display_name,
    calculation_spec_from_legacy,
    stage_display_name,
    theory_display_name,
    validate_workflow_spec,
    workflow_display_name,
    workflow_spec_from_calculation_spec,
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
    return calculation_plan_from_workflow_spec(
        workflow_spec_from_calculation_spec(spec)
    )


def calculation_plan_from_workflow_spec(workflow: WorkflowSpec) -> list[str]:
    normalized = validate_workflow_spec(workflow)
    theories = {stage.theory for stage in normalized.stages}
    show_theory = len(theories) > 1
    return [
        (
            f"{stage_display_name(stage)} ({theory_display_name(stage.theory)})"
            if show_theory
            else stage_display_name(stage)
        )
        for stage in normalized.stages
    ]


def _coerce_calculation_spec(calculation) -> CalculationSpec:
    if isinstance(calculation, CalculationSpec):
        return calculation

    return calculation_spec_from_legacy(calculation)


def _coerce_workflow_spec(calculation) -> WorkflowSpec:
    if isinstance(calculation, WorkflowSpec):
        return validate_workflow_spec(calculation)

    return workflow_spec_from_calculation_spec(_coerce_calculation_spec(calculation))


def summarize_workflow(flow, calculation):
    """
    Return a template-friendly summary of a Jobflow Flow.
    """

    workflow_spec = _coerce_workflow_spec(calculation)
    compatible_spec = calculation_spec_from_workflow_spec(workflow_spec)
    jobs = list(getattr(flow, "jobs", []) or [])
    theories = {stage.theory for stage in workflow_spec.stages}
    if len(theories) == 1:
        theory = next(iter(theories))
        theory_value = theory.value
        theory_label = theory_display_name(theory)
    else:
        theory_value = "mixed"
        theory_label = "Mixed"
    calculation_type = (
        workflow_label_from_spec(compatible_spec)
        if compatible_spec is not None
        else workflow_display_name(workflow_spec)
    )

    return {
        "calculation_type": calculation_type,
        "workflow_type": calculation_type,
        "flow_name": getattr(flow, "name", "Unknown"),
        "number_of_jobs": len(jobs),
        "job_names": [getattr(job, "name", "Unknown") for job in jobs],
        "calculation_plan": calculation_plan_from_workflow_spec(workflow_spec),
        "theory": theory_value,
        "theory_label": theory_label,
        "ready_for_submission": True,
    }


__all__ = [
    "summarize_workflow",
    "calculation_plan_from_spec",
    "calculation_plan_from_workflow_spec",
    "workflow_label_from_spec",
]
