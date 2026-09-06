from __future__ import annotations

import builtins
import json
import socket
import subprocess
import sys
from pathlib import Path

from pymatgen.core import Lattice, Structure

from backend.calculations.input_reference import build_input_reference_payload
from backend.calculations.method_considerations import (
    ALREADY_SELECTED,
    BI_PRESENT_DETECTION_ID,
    BI_SOC_CONSIDERATION_ID,
    NOT_SELECTED,
    SOC_RECOMMENDED_STATUS,
    UNSUPPORTED_FOR_WORKFLOW,
    WORKFLOW_NOT_PROVIDED,
    detect_structure_features,
    method_consideration_payload,
    method_considerations_for_structure,
)
from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.parser import parse_structure


BI_POSCAR = """Bi
4.75
1.0 0.0 0.0
0.0 1.0 0.0
0.0 0.0 1.0
Bi
1
direct
0.0 0.0 0.0
"""

SI_POSCAR_WITH_BI_COMMENT = """Bi appears only in this POSCAR comment
5.43
0.0 0.5 0.5
0.5 0.0 0.5
0.5 0.5 0.0
Si
2
direct
0.0 0.0 0.0
0.25 0.25 0.25
"""


def bi_structure() -> Structure:
    return Structure(Lattice.cubic(4.75), ["Bi"], [[0, 0, 0]])


def si_structure() -> Structure:
    return Structure(
        Lattice.cubic(5.43),
        ["Si", "Si"],
        [[0, 0, 0], [0.25, 0.25, 0.25]],
    )


def pbe_static_workflow(*, soc: bool = False) -> WorkflowSpec:
    modifiers = {Modifier.SOC} if soc else set()
    return WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE, modifiers)])


def only_consideration(structure, *, workflow=None):
    considerations = method_considerations_for_structure(structure, workflow=workflow)
    assert len(considerations) == 1
    return considerations[0]


def test_bi_structure_produces_factual_bi_detection():
    detections = detect_structure_features(bi_structure())

    assert len(detections) == 1
    detection = detections[0]
    assert detection.id == BI_PRESENT_DETECTION_ID
    assert detection.type == "element_present"
    assert detection.element == "Bi"
    assert detection.observed is True
    assert detection.observed_evidence == {
        "element": "Bi",
        "elements": ["Bi"],
    }
    assert detection.to_dict()["source"] == "pymatgen.Structure.composition.elements"


def test_bi_detection_produces_single_conservative_soc_consideration():
    consideration = only_consideration(bi_structure())

    assert consideration.id == BI_SOC_CONSIDERATION_ID
    assert consideration.method == "soc"
    assert consideration.modifier == "soc"
    assert consideration.status == SOC_RECOMMENDED_STATUS
    assert consideration.trigger_detection_id == BI_PRESENT_DETECTION_ID
    assert consideration.selection_state == WORKFLOW_NOT_PROVIDED
    assert consideration.applicable_stage_types == ("static",)
    assert consideration.bmd_compute_support["supported_stage_capabilities"] == [
        {
            "stage_type": "static",
            "stage_label": "Static Energy",
            "theory": "pbe",
            "theory_label": "PBE",
        }
    ]
    assert consideration.bmd_compute_support["workflow"] == {
        "provided": False,
        "support_status": WORKFLOW_NOT_PROVIDED,
        "selection_state": WORKFLOW_NOT_PROVIDED,
        "selected_stage_indices": [],
        "supported_stage_indices": [],
        "unsupported_selected_stage_indices": [],
    }
    reason = consideration.reason.lower()
    assert "may be relevant" in reason
    assert "required" not in reason
    assert "necessary" not in reason
    assert "invalid" not in reason
    assert consideration.policy_source["policy_version"] == 1
    assert consideration.policy_source["rule_id"] == BI_SOC_CONSIDERATION_ID


def test_si_structure_produces_no_bi_triggered_consideration():
    assert detect_structure_features(si_structure()) == ()
    assert method_considerations_for_structure(si_structure()) == ()
    assert method_consideration_payload(si_structure())["considerations"] == []


def test_bi_is_detected_from_actual_structure_species_not_poscar_comments():
    structure = parse_structure(SI_POSCAR_WITH_BI_COMMENT, "poscar")

    assert structure.composition.reduced_formula == "Si"
    assert detect_structure_features(structure) == ()
    assert method_considerations_for_structure(structure) == ()


def test_bi_without_workflow_remains_structure_first_without_workflow_guessing():
    payload = method_consideration_payload(bi_structure())
    consideration = payload["considerations"][0]

    assert payload["detections"][0]["id"] == BI_PRESENT_DETECTION_ID
    assert consideration["selection_state"] == WORKFLOW_NOT_PROVIDED
    assert consideration["bmd_compute_support"]["workflow"]["provided"] is False
    assert consideration["bmd_compute_support"]["workflow"]["supported_stage_indices"] == []


def test_bi_compatible_workflow_without_soc_is_not_selected():
    consideration = only_consideration(
        bi_structure(),
        workflow=pbe_static_workflow(),
    )

    assert consideration.selection_state == NOT_SELECTED
    assert consideration.bmd_compute_support["workflow"]["provided"] is True
    assert consideration.bmd_compute_support["workflow"]["supported_stage_indices"] == [1]
    assert consideration.bmd_compute_support["workflow"]["selected_stage_indices"] == []


def test_bi_compatible_workflow_with_soc_is_already_selected():
    consideration = only_consideration(
        bi_structure(),
        workflow=pbe_static_workflow(soc=True),
    )

    assert consideration.selection_state == ALREADY_SELECTED
    assert consideration.bmd_compute_support["workflow"]["supported_stage_indices"] == [1]
    assert consideration.bmd_compute_support["workflow"]["selected_stage_indices"] == [1]


def test_incompatible_workflow_support_is_represented_without_workflow_changes():
    for workflow in (
        WorkflowSpec([StageSpec(StageType.RELAX, Theory.PBE)]),
        WorkflowSpec([StageSpec(StageType.STATIC, Theory.HSE06)]),
    ):
        before = workflow.to_dict()
        consideration = only_consideration(bi_structure(), workflow=workflow)

        assert consideration.selection_state == UNSUPPORTED_FOR_WORKFLOW
        assert consideration.bmd_compute_support["workflow"]["support_status"] == (
            UNSUPPORTED_FOR_WORKFLOW
        )
        assert consideration.bmd_compute_support["workflow"]["supported_stage_indices"] == []
        assert workflow.to_dict() == before


def test_consideration_analysis_leaves_workflow_and_structure_unchanged():
    structure = bi_structure()
    workflow = pbe_static_workflow()
    structure_before = structure.as_dict()
    workflow_before = workflow.to_dict()

    method_consideration_payload(structure, workflow=workflow)

    assert structure.as_dict() == structure_before
    assert workflow.to_dict() == workflow_before
    assert workflow.stages[0].modifiers == frozenset()


def test_consideration_analysis_does_not_change_generated_vasp_reference():
    workflow = pbe_static_workflow()
    request = {
        "structure": {
            "type": "pasted_text",
            "format": "poscar",
            "text": BI_POSCAR,
        },
        "workflow_spec": workflow.to_dict(),
        "resources": {"ntasks": 24, "mem_gb": 128},
        "potcar_functional": "PBE_64",
    }
    before = build_input_reference_payload(request, include_provenance=False)

    method_consideration_payload(parse_structure(BI_POSCAR), workflow=workflow)

    after = build_input_reference_payload(request, include_provenance=False)
    assert after["request"] == before["request"]
    assert after["workflow"] == before["workflow"]
    assert after["reference"] == before["reference"]


def test_consideration_analysis_does_not_touch_io_network_or_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("method consideration analysis must be pure")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    payload = method_consideration_payload(
        bi_structure(),
        workflow=pbe_static_workflow(),
    )

    assert payload["considerations"][0]["selection_state"] == NOT_SELECTED


def test_consideration_generation_does_not_import_runtime_machinery():
    code = """
import json
import sys
from pymatgen.core import Lattice, Structure
from backend.calculations.method_considerations import method_consideration_payload

structure = Structure(Lattice.cubic(4.75), ["Bi"], [[0, 0, 0]])
method_consideration_payload(structure)
blocked = [
    "atomate2",
    "custodian",
    "fastapi",
    "jobflow",
    "paramiko",
    "backend.execution",
    "backend.generated_inputs",
    "backend.paramiko_remote",
    "backend.remote",
    "backend.remote_preparation",
    "backend.remote_submission",
    "backend.submission",
    "backend.workflows",
]
print(json.dumps({name: name in sys.modules for name in blocked}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        check=True,
        text=True,
    )

    loaded = json.loads(completed.stdout)
    assert loaded == {name: False for name in loaded}


def test_consideration_payload_is_json_safe_and_deterministic():
    workflow = pbe_static_workflow()
    first = method_consideration_payload(bi_structure(), workflow=workflow)
    second = method_consideration_payload(bi_structure(), workflow=workflow)

    assert first == second
    assert json.loads(json.dumps(first, sort_keys=True)) == first
    assert method_considerations_for_structure(
        bi_structure(),
        workflow=workflow,
    ) == method_considerations_for_structure(
        bi_structure(),
        workflow=workflow,
    )
