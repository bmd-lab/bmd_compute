from __future__ import annotations

from backend.calculations.models import CalculationSpec, Theory
from backend.calculations.registry import (
    calculation_spec_from_legacy,
    legacy_potcar_functional_from_spec,
    validate_calculation_spec,
)


def build_calculation_flow(
    structure,
    spec: CalculationSpec,
    *,
    label: str = "vasp_run",
    incar: dict | None = None,
    kpoints: dict | None = None,
    potcar_functional: str | None = None,
):
    """
    Build a Jobflow Flow for a calculation intent.

    This is currently a compatibility adapter. It validates the new
    CalculationSpec object and delegates to backend.workflows so this PR does
    not change atomate2 makers, pymatgen input sets, or Burton Lab overrides.
    """

    normalized = CalculationSpec(
        purpose=spec.purpose,
        theory=spec.theory,
        modifiers=spec.modifiers,
        label=spec.label,
    )

    if normalized.theory is Theory.PBE:
        return _build_pbe_calculation_flow(
            structure=structure,
            spec=normalized,
            label=label,
            incar=incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    raise NotImplementedError(
        f"Theory '{normalized.theory.value}' is not implemented in the native "
        "CalculationSpec builder yet."
    )


def _build_pbe_calculation_flow(
    structure,
    spec: CalculationSpec,
    *,
    label: str,
    incar: dict | None,
    kpoints: dict | None,
    potcar_functional: str | None,
):
    normalized = validate_calculation_spec(spec)
    legacy_potcar_functional = potcar_functional or legacy_potcar_functional_from_spec(
        normalized
    )

    from backend.workflows import build_atomate2_flow_for_spec

    return build_atomate2_flow_for_spec(
        structure=structure,
        spec=normalized,
        label=label,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=legacy_potcar_functional,
    )


def build_calculation_flow_from_legacy(
    structure,
    workflow: str | None,
    *,
    label: str = "vasp_run",
    incar: dict | None = None,
    kpoints: dict | None = None,
    potcar_functional: str | None = None,
):
    spec = calculation_spec_from_legacy(workflow, potcar_functional)
    return build_calculation_flow(
        structure,
        spec,
        label=label,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )


__all__ = [
    "build_calculation_flow",
    "build_calculation_flow_from_legacy",
]
