from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.calculations.registry import (
    CalculationValidationError,
    modifier_display_name,
    stage_display_name,
    theory_display_name,
    validate_stage_spec,
    validate_workflow_spec,
)


"""
Structure-aware method considerations for BMD Compute.

This module describes deterministic BMD Compute policy about optional VASP
treatments that may be worth considering for a submitted structure. It does
not modify workflows, generated inputs, or execution state.
"""


POLICY_VERSION = 2
SOC_HEAVY_ELEMENTS_CONSIDERATION_ID = "soc.heavy_elements"
SOC_RECOMMENDED_STATUS = "recommended_for_consideration"
WORKFLOW_NOT_PROVIDED = "workflow_not_provided"
NOT_SELECTED = "not_selected"
ALREADY_SELECTED = "already_selected"
UNSUPPORTED_FOR_WORKFLOW = "unsupported_for_workflow"
INVALID_WORKFLOW = "invalid_workflow"

_ELEMENT_PRESENT_DETECTION_TYPE = "element_present"


def _build_soc_trigger_element_classes(
    trigger_classes: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    element_classes: dict[str, list[str]] = {}
    for class_name, elements in trigger_classes.items():
        for element in elements:
            element_classes.setdefault(element, []).append(class_name)
    return {
        element: tuple(classes)
        for element, classes in sorted(element_classes.items())
    }


SOC_TRIGGER_CLASSES: Mapping[str, tuple[str, ...]] = {
    "4d_transition_metals": ("Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd"),
    "5d_transition_metals": ("Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg"),
    "lanthanides": (
        "La",
        "Ce",
        "Pr",
        "Nd",
        "Pm",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "Lu",
    ),
    "actinides": (
        "Ac",
        "Th",
        "Pa",
        "U",
        "Np",
        "Pu",
        "Am",
        "Cm",
        "Bk",
        "Cf",
        "Es",
        "Fm",
        "Md",
        "No",
        "Lr",
    ),
    "heavy_p_block": ("Tl", "Pb", "Bi", "Po"),
}
SOC_TRIGGER_ELEMENT_CLASSES: Mapping[str, tuple[str, ...]] = (
    _build_soc_trigger_element_classes(SOC_TRIGGER_CLASSES)
)

_POLICY_SOURCE = {
    "policy_id": "bmd_compute.method_considerations",
    "policy_version": POLICY_VERSION,
    "implementation": "backend.calculations.method_considerations",
    "authority": "BMD Compute executable method-consideration policy",
}
_HEAVY_ELEMENT_SOC_REASON = (
    "This structure contains one or more heavy elements for which spin-orbit "
    "coupling may be important. Consider SOC when relativistic effects may "
    "materially affect the calculated properties."
)
_HEAVY_ELEMENT_SOC_LIMITATIONS = (
    "This is a composition-based screening consideration. Elemental presence "
    "alone does not establish that SOC materially affects the property of interest.",
    "This analysis does not determine oxidation state, bonding, band character, "
    "orbital contributions, or whether SOC is required.",
)


@dataclass(frozen=True)
class StructureDetection:
    """
    Factual structure/composition evidence derived from a pymatgen Structure.
    """

    id: str
    type: str
    observed: bool
    element: str
    observed_evidence: Mapping[str, Any] = field(default_factory=dict)
    soc_trigger_classes: tuple[str, ...] = field(default_factory=tuple)
    source: str = "pymatgen.Structure.composition.elements"

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_evidence", _json_safe_dict(self.observed_evidence))
        object.__setattr__(
            self,
            "soc_trigger_classes",
            tuple(str(item) for item in self.soc_trigger_classes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "observed": self.observed,
            "element": self.element,
            "soc_trigger_classes": list(self.soc_trigger_classes),
            "observed_evidence": _json_safe_value(self.observed_evidence),
            "source": self.source,
        }


@dataclass(frozen=True)
class MethodConsideration:
    """
    BMD Compute policy applied to factual structure evidence.
    """

    id: str
    method: str
    modifier: str
    status: str
    trigger_detection_ids: tuple[str, ...]
    trigger_elements: tuple[str, ...]
    trigger_classes: tuple[str, ...]
    observed_evidence: Mapping[str, Any]
    reason: str
    applicable_stage_types: tuple[str, ...]
    bmd_compute_support: Mapping[str, Any]
    selection_state: str
    policy_source: Mapping[str, Any]
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trigger_detection_ids",
            tuple(str(item) for item in self.trigger_detection_ids),
        )
        object.__setattr__(
            self,
            "trigger_elements",
            tuple(str(item) for item in self.trigger_elements),
        )
        object.__setattr__(
            self,
            "trigger_classes",
            tuple(str(item) for item in self.trigger_classes),
        )
        object.__setattr__(self, "observed_evidence", _json_safe_dict(self.observed_evidence))
        object.__setattr__(self, "applicable_stage_types", tuple(self.applicable_stage_types))
        object.__setattr__(self, "bmd_compute_support", _json_safe_dict(self.bmd_compute_support))
        object.__setattr__(self, "policy_source", _json_safe_dict(self.policy_source))
        object.__setattr__(self, "limitations", tuple(str(item) for item in self.limitations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "modifier": self.modifier,
            "status": self.status,
            "trigger_detection_ids": list(self.trigger_detection_ids),
            "trigger_elements": list(self.trigger_elements),
            "trigger_classes": list(self.trigger_classes),
            "observed_evidence": _json_safe_value(self.observed_evidence),
            "reason": self.reason,
            "applicable_stage_types": list(self.applicable_stage_types),
            "bmd_compute_support": _json_safe_value(self.bmd_compute_support),
            "selection_state": self.selection_state,
            "policy_source": _json_safe_value(self.policy_source),
            "limitations": list(self.limitations),
        }


def detect_structure_features(structure) -> tuple[StructureDetection, ...]:
    """
    Return factual detections from an already parsed pymatgen Structure.
    """

    elements = _structure_element_symbols(structure)
    detections: list[StructureDetection] = []
    for element in elements:
        trigger_classes = _soc_trigger_classes_for_element(element)
        if not trigger_classes:
            continue
        detections.append(
            StructureDetection(
                id=_element_detection_id(element),
                type=_ELEMENT_PRESENT_DETECTION_TYPE,
                observed=True,
                element=element,
                soc_trigger_classes=trigger_classes,
                observed_evidence={
                    "element": element,
                    "elements": list(elements),
                    "soc_trigger_classes": list(trigger_classes),
                },
            )
        )
    return tuple(detections)


def method_considerations_for_structure(
    structure,
    *,
    workflow: WorkflowSpec | Mapping[str, Any] | None = None,
) -> tuple[MethodConsideration, ...]:
    detections = detect_structure_features(structure)
    return method_considerations_from_detections(detections, workflow=workflow)


def method_considerations_from_detections(
    detections: tuple[StructureDetection, ...] | list[StructureDetection],
    *,
    workflow: WorkflowSpec | Mapping[str, Any] | None = None,
) -> tuple[MethodConsideration, ...]:
    considerations: list[MethodConsideration] = []
    workflow_spec = _coerce_workflow_spec(workflow)
    soc_detections = tuple(
        detection
        for detection in detections
        if detection.observed
        and detection.type == _ELEMENT_PRESENT_DETECTION_TYPE
        and _soc_trigger_classes_for_element(detection.element)
    )
    if soc_detections:
        considerations.append(_heavy_element_soc_consideration(soc_detections, workflow_spec))
    return tuple(considerations)


def method_consideration_payload(
    structure,
    *,
    workflow: WorkflowSpec | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    detections = detect_structure_features(structure)
    considerations = method_considerations_from_detections(detections, workflow=workflow)
    return {
        "policy_version": POLICY_VERSION,
        "scope": "BMD Compute structure-aware method considerations; observation only",
        "detections": [detection.to_dict() for detection in detections],
        "considerations": [
            consideration.to_dict()
            for consideration in considerations
        ],
    }


def _heavy_element_soc_consideration(
    detections: tuple[StructureDetection, ...],
    workflow: WorkflowSpec | None,
) -> MethodConsideration:
    support = _soc_support_payload(workflow)
    trigger_detection_ids = tuple(detection.id for detection in detections)
    trigger_elements = tuple(detection.element for detection in detections)
    trigger_classes = _ordered_soc_trigger_classes(detections)
    return MethodConsideration(
        id=SOC_HEAVY_ELEMENTS_CONSIDERATION_ID,
        method="soc",
        modifier=Modifier.SOC.value,
        status=SOC_RECOMMENDED_STATUS,
        trigger_detection_ids=trigger_detection_ids,
        trigger_elements=trigger_elements,
        trigger_classes=trigger_classes,
        observed_evidence={
            "trigger_detection_ids": list(trigger_detection_ids),
            "trigger_elements": list(trigger_elements),
            "trigger_classes": list(trigger_classes),
            "detections": [detection.to_dict() for detection in detections],
        },
        reason=_HEAVY_ELEMENT_SOC_REASON,
        applicable_stage_types=tuple(
            sorted(
                {
                    capability["stage_type"]
                    for capability in support["supported_stage_capabilities"]
                }
            )
        ),
        bmd_compute_support=support,
        selection_state=support["workflow"]["selection_state"],
        policy_source={
            **_POLICY_SOURCE,
            "rule_id": SOC_HEAVY_ELEMENTS_CONSIDERATION_ID,
        },
        limitations=_HEAVY_ELEMENT_SOC_LIMITATIONS,
    )


def _soc_support_payload(workflow: WorkflowSpec | None) -> dict[str, Any]:
    capabilities = _supported_modifier_stage_capabilities(Modifier.SOC)
    workflow_support = _workflow_modifier_support(workflow, Modifier.SOC)
    return {
        "modifier": Modifier.SOC.value,
        "modifier_label": modifier_display_name(Modifier.SOC),
        "global_supported": bool(capabilities),
        "supported_stage_capabilities": capabilities,
        "workflow": workflow_support,
    }


def _supported_modifier_stage_capabilities(modifier: Modifier) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []
    for stage_type in sorted(StageType, key=lambda item: item.value):
        for theory in sorted(Theory, key=lambda item: item.value):
            stage = StageSpec(stage_type, theory, {modifier})
            if _stage_is_supported(stage):
                capabilities.append(
                    {
                        "stage_type": stage_type.value,
                        "stage_label": stage_display_name(stage_type),
                        "theory": theory.value,
                        "theory_label": theory_display_name(theory),
                    }
                )
    return capabilities


def _workflow_modifier_support(
    workflow: WorkflowSpec | None,
    modifier: Modifier,
) -> dict[str, Any]:
    if workflow is None:
        return {
            "provided": False,
            "support_status": WORKFLOW_NOT_PROVIDED,
            "selection_state": WORKFLOW_NOT_PROVIDED,
            "selected_stage_indices": [],
            "supported_stage_indices": [],
            "unsupported_selected_stage_indices": [],
        }

    try:
        normalized = validate_workflow_spec(workflow)
    except CalculationValidationError as exc:
        return {
            "provided": True,
            "support_status": INVALID_WORKFLOW,
            "selection_state": INVALID_WORKFLOW,
            "selected_stage_indices": _selected_stage_indices(workflow, modifier),
            "supported_stage_indices": [],
            "unsupported_selected_stage_indices": [],
            "message": exc.message,
            "suggestion": exc.suggestion,
        }

    selected_supported: list[int] = []
    selected_unsupported: list[int] = []
    supported_candidates: list[int] = []
    for index, stage in enumerate(normalized.stages, start=1):
        has_modifier = modifier in stage.modifiers
        if has_modifier:
            if _stage_is_supported(stage):
                selected_supported.append(index)
                supported_candidates.append(index)
            else:
                selected_unsupported.append(index)
        elif _stage_is_supported(_stage_with_modifier(stage, modifier)):
            supported_candidates.append(index)

    supported_candidates = sorted(set(supported_candidates))
    if selected_supported:
        selection_state = ALREADY_SELECTED
    elif supported_candidates:
        selection_state = NOT_SELECTED
    else:
        selection_state = UNSUPPORTED_FOR_WORKFLOW

    return {
        "provided": True,
        "support_status": selection_state,
        "selection_state": selection_state,
        "selected_stage_indices": selected_supported,
        "supported_stage_indices": supported_candidates,
        "unsupported_selected_stage_indices": selected_unsupported,
    }


def _stage_with_modifier(stage: StageSpec, modifier: Modifier) -> StageSpec:
    return StageSpec(
        stage_type=stage.stage_type,
        theory=stage.theory,
        modifiers=frozenset({*stage.modifiers, modifier}),
        label=stage.label,
        options=stage.options,
    )


def _selected_stage_indices(workflow: WorkflowSpec, modifier: Modifier) -> list[int]:
    return [
        index
        for index, stage in enumerate(workflow.stages, start=1)
        if modifier in stage.modifiers
    ]


def _stage_is_supported(stage: StageSpec) -> bool:
    try:
        validate_stage_spec(stage)
    except CalculationValidationError:
        return False
    return True


def _coerce_workflow_spec(
    workflow: WorkflowSpec | Mapping[str, Any] | None,
) -> WorkflowSpec | None:
    if workflow is None:
        return None
    if isinstance(workflow, WorkflowSpec):
        return workflow
    if isinstance(workflow, Mapping):
        return WorkflowSpec.from_dict(dict(workflow))
    raise TypeError(f"Unsupported workflow type: {type(workflow).__name__}")


def _structure_element_symbols(structure) -> tuple[str, ...]:
    composition = getattr(structure, "composition", None)
    elements = getattr(composition, "elements", None) or ()
    symbols = {
        str(getattr(element, "symbol", element))
        for element in elements
    }
    return tuple(sorted(symbols))


def _element_detection_id(element: str) -> str:
    return f"element.{element.lower()}.present"


def _soc_trigger_classes_for_element(element: str) -> tuple[str, ...]:
    return SOC_TRIGGER_ELEMENT_CLASSES.get(str(element), ())


def _ordered_soc_trigger_classes(
    detections: tuple[StructureDetection, ...],
) -> tuple[str, ...]:
    observed_classes = {
        class_name
        for detection in detections
        for class_name in _soc_trigger_classes_for_element(detection.element)
    }
    return tuple(
        class_name
        for class_name in SOC_TRIGGER_CLASSES
        if class_name in observed_classes
    )


def _json_safe_dict(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_safe_value(value)
        for key, value in sorted(dict(mapping or {}).items(), key=lambda item: str(item[0]))
    }


def _json_safe_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _json_safe_dict(value)
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    raise TypeError(f"Value is not JSON-native: {type(value).__name__}")


__all__ = [
    "ALREADY_SELECTED",
    "INVALID_WORKFLOW",
    "MethodConsideration",
    "NOT_SELECTED",
    "POLICY_VERSION",
    "SOC_HEAVY_ELEMENTS_CONSIDERATION_ID",
    "SOC_RECOMMENDED_STATUS",
    "SOC_TRIGGER_CLASSES",
    "SOC_TRIGGER_ELEMENT_CLASSES",
    "StructureDetection",
    "UNSUPPORTED_FOR_WORKFLOW",
    "WORKFLOW_NOT_PROVIDED",
    "detect_structure_features",
    "method_consideration_payload",
    "method_considerations_for_structure",
    "method_considerations_from_detections",
]
