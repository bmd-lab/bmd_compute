from __future__ import annotations

from itertools import combinations

from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory


class CalculationValidationError(ValueError):
    def __init__(self, message: str, *, suggestion: str | None = None):
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion


_ACTIVE_UI_MODIFIERS = (
    Modifier.SPIN_POLARIZED,
    Modifier.SOC,
    Modifier.DFT_U,
    Modifier.GAMMA_ONLY,
)


def _modifier_subsets(
    modifiers: tuple[Modifier, ...],
) -> tuple[frozenset[Modifier], ...]:
    return tuple(
        frozenset(subset)
        for size in range(len(modifiers) + 1)
        for subset in combinations(modifiers, size)
    )


def _build_supported_compatibility_workflows() -> dict[
    tuple[Purpose, Theory, frozenset[Modifier]],
    str,
]:
    supported: dict[tuple[Purpose, Theory, frozenset[Modifier]], str] = {}

    for modifiers in _modifier_subsets(_ACTIVE_UI_MODIFIERS):
        supported[(Purpose.STATIC, Theory.PBE, modifiers)] = "static"
        supported[(Purpose.RELAX, Theory.PBE, modifiers)] = "relax"
        supported[(Purpose.DOUBLE_RELAX, Theory.PBE, modifiers)] = "double_relax"
        supported[(Purpose.DOS, Theory.PBE, modifiers)] = "dos"
        if Modifier.GAMMA_ONLY not in modifiers:
            supported[(Purpose.BAND_STRUCTURE, Theory.PBE, modifiers)] = "band_structure"
        supported[
            (
                Purpose.RELAX,
                Theory.PBE,
                frozenset({*modifiers, Modifier.IONS_ONLY}),
            )
        ] = "relax_ions"

    return supported


_SUPPORTED_COMPATIBILITY_WORKFLOWS = _build_supported_compatibility_workflows()

_LEGACY_WORKFLOW_SPECS = {
    "static": CalculationSpec(Purpose.STATIC, Theory.PBE),
    "relax": CalculationSpec(Purpose.RELAX, Theory.PBE),
    "double_relax": CalculationSpec(Purpose.DOUBLE_RELAX, Theory.PBE),
    "dos": CalculationSpec(Purpose.DOS, Theory.PBE),
    "band_structure": CalculationSpec(Purpose.BAND_STRUCTURE, Theory.PBE),
    "relax_ions": CalculationSpec(Purpose.RELAX, Theory.PBE, {Modifier.IONS_ONLY}),
}

_LEGACY_POTCAR_THEORIES = {
    "PBE": Theory.PBE,
    "PBE_52": Theory.PBE,
    "PBE_54": Theory.PBE,
    "PBE_64": Theory.PBE,
}

_THEORY_DEFAULT_POTCAR_FUNCTIONAL = {
    Theory.PBE: "PBE_64",
}

_PURPOSE_DISPLAY_NAMES = {
    Purpose.RELAX: "Geometry Optimisation",
    Purpose.DOUBLE_RELAX: "Double Geometry Optimisation",
    Purpose.STATIC: "Static Energy",
    Purpose.DOS: "Density of States",
    Purpose.BAND_STRUCTURE: "Band Structure",
    Purpose.DIELECTRIC: "Dielectric Properties",
}

_PURPOSE_DESCRIPTIONS = {
    Purpose.RELAX: "Optimise the atomic structure before analysis or follow-up calculations.",
    Purpose.DOUBLE_RELAX: "Run two consecutive geometry optimisations, using the first final structure as the second starting structure.",
    Purpose.STATIC: "Calculate a single-point total energy for the supplied structure.",
    Purpose.DOS: "Calculate the electronic density of states.",
    Purpose.BAND_STRUCTURE: "Calculate the electronic band structure.",
    Purpose.DIELECTRIC: "Calculate dielectric response properties.",
}

_PURPOSE_STAGE_DIRECTORIES = {
    Purpose.DOUBLE_RELAX: ("relax_01", "relax_02"),
    Purpose.DOS: ("stage_01", "stage_02", "stage_03"),
    Purpose.BAND_STRUCTURE: ("stage_01", "stage_02", "stage_03"),
}

_THEORY_DISPLAY_NAMES = {
    Theory.PBE: "PBE",
    Theory.R2SCAN: "r2SCAN",
    Theory.HSE06: "HSE06",
}

_MODIFIER_DISPLAY_NAMES = {
    Modifier.SPIN_POLARIZED: "Spin Polarised",
    Modifier.SOC: "Spin-Orbit Coupling (SOC)",
    Modifier.DFT_U: "DFT+U",
    Modifier.GAMMA_ONLY: "Gamma-only",
    Modifier.IONS_ONLY: "Ions only",
}

_UNIMPLEMENTED_TOOLTIP = "Coming soon"

_UI_HIDDEN_MODIFIERS = {
    Modifier.IONS_ONLY,
}

_UI_MODIFIER_ORDER = (
    Modifier.SPIN_POLARIZED,
    Modifier.SOC,
    Modifier.DFT_U,
    Modifier.GAMMA_ONLY,
)


def validate_calculation_spec(spec: CalculationSpec) -> CalculationSpec:
    normalized = CalculationSpec(
        purpose=spec.purpose,
        theory=spec.theory,
        modifiers=spec.modifiers,
        label=spec.label,
    )
    key = _compatibility_key(normalized)

    if key not in _SUPPORTED_COMPATIBILITY_WORKFLOWS:
        supported = ", ".join(
            _format_combination(*combination)
            for combination in _SUPPORTED_COMPATIBILITY_WORKFLOWS
        )
        raise CalculationValidationError(
            "Calculation combination is not implemented in the compatibility "
            f"builder: {_format_combination(*key)}. Supported combinations: {supported}."
        )

    return normalized


def calculation_spec_from_legacy(
    workflow: str | None,
    potcar_functional: str | None = None,
) -> CalculationSpec:
    workflow_name = (workflow or "static").strip().lower()
    try:
        spec = _LEGACY_WORKFLOW_SPECS[workflow_name]
    except KeyError as exc:
        raise CalculationValidationError(f"Unsupported legacy workflow: {workflow!r}") from exc

    theory = _theory_from_legacy_potcar(potcar_functional)
    return validate_calculation_spec(
        CalculationSpec(
            purpose=spec.purpose,
            theory=theory,
            modifiers=spec.modifiers,
            label=spec.label,
        )
    )


def calculation_spec_from_flow_spec(flow_spec: dict | None) -> CalculationSpec:
    values = dict(flow_spec or {})
    if values.get("calculation_spec"):
        return validate_calculation_spec(
            CalculationSpec.from_dict(values.get("calculation_spec"))
        )

    return calculation_spec_from_legacy(
        values.get("workflow"),
        values.get("potcar_functional"),
    )


def legacy_workflow_from_spec(spec: CalculationSpec) -> str:
    normalized = validate_calculation_spec(spec)
    return _SUPPORTED_COMPATIBILITY_WORKFLOWS[_compatibility_key(normalized)]


def legacy_potcar_functional_from_spec(spec: CalculationSpec) -> str:
    normalized = validate_calculation_spec(spec)
    try:
        return _THEORY_DEFAULT_POTCAR_FUNCTIONAL[normalized.theory]
    except KeyError as exc:
        raise CalculationValidationError(
            f"No legacy POTCAR functional for theory: {normalized.theory.value}"
        ) from exc


def calculation_stage_directories(spec: CalculationSpec) -> tuple[str, ...]:
    normalized = validate_calculation_spec(spec)
    return _PURPOSE_STAGE_DIRECTORIES.get(normalized.purpose, ())


def calculation_result_stage_directory(spec: CalculationSpec) -> str | None:
    stage_directories = calculation_stage_directories(spec)
    if not stage_directories:
        return None

    return stage_directories[-1]


def supported_combinations() -> tuple[CalculationSpec, ...]:
    return tuple(
        CalculationSpec(purpose=purpose, theory=theory, modifiers=modifiers)
        for purpose, theory, modifiers in _SUPPORTED_COMPATIBILITY_WORKFLOWS
    )


def calculation_display_name(spec: CalculationSpec) -> str:
    normalized = CalculationSpec(
        purpose=spec.purpose,
        theory=spec.theory,
        modifiers=spec.modifiers,
        label=spec.label,
    )
    return _PURPOSE_DISPLAY_NAMES.get(
        normalized.purpose,
        normalized.purpose.value.replace("_", " ").title(),
    )


def theory_display_name(theory: Theory | str) -> str:
    normalized = Theory.from_value(theory)
    return _THEORY_DISPLAY_NAMES.get(
        normalized,
        normalized.value.replace("_", " ").title(),
    )


def modifier_display_name(modifier: Modifier | str) -> str:
    normalized = Modifier.from_value(modifier)
    return _MODIFIER_DISPLAY_NAMES.get(
        normalized,
        normalized.value.replace("_", " ").title(),
    )


def calculation_form_options() -> dict:
    supported = supported_combinations()
    visible_supported = [
        spec for spec in supported
        if not spec.modifiers.intersection(_UI_HIDDEN_MODIFIERS)
    ]
    supported_purposes = {spec.purpose for spec in visible_supported}
    supported_theories = {spec.theory for spec in visible_supported}
    supported_modifiers = {
        modifier
        for spec in visible_supported
        for modifier in spec.modifiers
        if modifier not in _UI_HIDDEN_MODIFIERS
    }

    return {
        "purposes": [
            {
                "value": purpose.value,
                "label": _PURPOSE_DISPLAY_NAMES.get(
                    purpose,
                    purpose.value.replace("_", " ").title(),
                ),
                "description": _PURPOSE_DESCRIPTIONS.get(purpose, ""),
                "enabled": purpose in supported_purposes,
            }
            for purpose in Purpose
            if purpose in supported_purposes
        ],
        "theories": [
            {
                "value": theory.value,
                "label": theory_display_name(theory),
                "enabled": theory in supported_theories,
            }
            for theory in Theory
            if theory in supported_theories
        ],
        "modifiers": [
            {
                "value": modifier.value,
                "label": modifier_display_name(modifier),
                "enabled": modifier in supported_modifiers,
                "tooltip": (
                    ""
                    if modifier in supported_modifiers
                    else _UNIMPLEMENTED_TOOLTIP
                ),
            }
            for modifier in _UI_MODIFIER_ORDER
            if modifier not in _UI_HIDDEN_MODIFIERS
        ],
    }


def _theory_from_legacy_potcar(potcar_functional: str | None) -> Theory:
    if not potcar_functional:
        return Theory.PBE

    key = str(potcar_functional).strip().upper()
    try:
        return _LEGACY_POTCAR_THEORIES[key]
    except KeyError as exc:
        raise CalculationValidationError(
            f"Unsupported legacy POTCAR functional: {potcar_functional!r}"
        ) from exc


def _compatibility_key(spec: CalculationSpec) -> tuple[Purpose, Theory, frozenset[Modifier]]:
    return (spec.purpose, spec.theory, frozenset(spec.modifiers))


def _format_combination(
    purpose: Purpose,
    theory: Theory,
    modifiers: frozenset[Modifier],
) -> str:
    modifier_text = ",".join(sorted(modifier.value for modifier in modifiers)) or "none"
    return f"purpose={purpose.value}, theory={theory.value}, modifiers={modifier_text}"


__all__ = [
    "CalculationValidationError",
    "calculation_display_name",
    "calculation_form_options",
    "calculation_result_stage_directory",
    "calculation_stage_directories",
    "calculation_spec_from_flow_spec",
    "calculation_spec_from_legacy",
    "legacy_potcar_functional_from_spec",
    "legacy_workflow_from_spec",
    "modifier_display_name",
    "supported_combinations",
    "theory_display_name",
    "validate_calculation_spec",
]
