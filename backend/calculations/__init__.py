from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "CalculationSpec": ("backend.calculations.models", "CalculationSpec"),
    "CalculationValidationError": (
        "backend.calculations.registry",
        "CalculationValidationError",
    ),
    "CalculationStage": ("backend.calculations.theory_policy", "CalculationStage"),
    "ALLOWED_CPU_COUNTS": ("backend.calculations.resources", "ALLOWED_CPU_COUNTS"),
    "ALLOWED_MEMORY_GB": ("backend.calculations.resources", "ALLOWED_MEMORY_GB"),
    "DEFAULT_NCORE": ("backend.calculations.resources", "DEFAULT_NCORE"),
    "ExecutionResources": ("backend.calculations.resources", "ExecutionResources"),
    "Modifier": ("backend.calculations.models", "Modifier"),
    "Purpose": ("backend.calculations.models", "Purpose"),
    "StageSpec": ("backend.calculations.models", "StageSpec"),
    "StageType": ("backend.calculations.models", "StageType"),
    "Theory": ("backend.calculations.models", "Theory"),
    "WorkflowSpec": ("backend.calculations.models", "WorkflowSpec"),
    "apply_theory_incar_settings": (
        "backend.calculations.theory_policy",
        "apply_theory_incar_settings",
    ),
    "build_calculation_flow": (
        "backend.calculations.builder",
        "build_calculation_flow",
    ),
    "calculation_spec_from_flow_spec": (
        "backend.calculations.registry",
        "calculation_spec_from_flow_spec",
    ),
    "calculation_spec_from_legacy": (
        "backend.calculations.registry",
        "calculation_spec_from_legacy",
    ),
    "default_execution_resources": (
        "backend.calculations.resources",
        "default_execution_resources",
    ),
    "describe_stage": (
        "backend.calculations.vasp_stage_definitions",
        "describe_stage",
    ),
    "legacy_potcar_functional_from_spec": (
        "backend.calculations.registry",
        "legacy_potcar_functional_from_spec",
    ),
    "legacy_workflow_from_spec": (
        "backend.calculations.registry",
        "legacy_workflow_from_spec",
    ),
    "list_stage_definitions": (
        "backend.calculations.vasp_stage_definitions",
        "list_stage_definitions",
    ),
    "ncore_for_execution_resources": (
        "backend.calculations.resources",
        "ncore_for_execution_resources",
    ),
    "normalize_execution_resources": (
        "backend.calculations.resources",
        "normalize_execution_resources",
    ),
    "theory_default_potcar_functional": (
        "backend.calculations.theory_policy",
        "theory_default_potcar_functional",
    ),
    "theory_incar_settings": (
        "backend.calculations.theory_policy",
        "theory_incar_settings",
    ),
    "validate_calculation_spec": (
        "backend.calculations.registry",
        "validate_calculation_spec",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})