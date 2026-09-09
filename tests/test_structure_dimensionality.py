from __future__ import annotations

import builtins
import inspect
import json

import pytest
from pymatgen.core import Lattice, Structure

import backend.structure_dimensionality as structure_dimensionality
from backend.calculations.models import StageSpec, StageType, Theory, WorkflowSpec
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from backend.structure_dimensionality import (
    ANALYSIS_FAILED,
    METHOD_ID,
    OBSERVED,
    StructureDimensionalityObservation,
    observe_structure_dimensionality,
)


SNS2_POSCAR = """SnS2
1.0
3.648 0.0 0.0
-1.824 3.159 0.0
0.0 0.0 5.899
Sn S
1 2
direct
0.0 0.0 0.0
0.3333333333333333 0.6666666666666666 0.25
0.6666666666666666 0.3333333333333333 0.75
"""

SI_POSCAR = """Si
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


def sns2_structure() -> Structure:
    return parse_structure(SNS2_POSCAR)


def si_structure() -> Structure:
    return parse_structure(SI_POSCAR)


def bi2se3_structure() -> Structure:
    return Structure.from_spacegroup(
        "R-3m",
        Lattice.hexagonal(4.143, 28.636),
        ["Bi", "Se", "Se"],
        [[0, 0, 0.399], [0, 0, 0.0], [0, 0, 0.211]],
    )


def mos2_structure() -> Structure:
    return Structure.from_spacegroup(
        "P6_3/mmc",
        Lattice.hexagonal(3.16, 12.30),
        ["Mo", "S"],
        [[1 / 3, 2 / 3, 1 / 4], [1 / 3, 2 / 3, 0.621]],
    )


def graphite_structure() -> Structure:
    return Structure(
        Lattice.hexagonal(2.46, 6.70),
        ["C", "C", "C", "C"],
        [[0, 0, 0], [1 / 3, 2 / 3, 0], [0, 0, 0.5], [2 / 3, 1 / 3, 0.5]],
    )


def hbn_structure() -> Structure:
    return Structure(
        Lattice.hexagonal(2.50, 6.66),
        ["B", "N", "B", "N"],
        [[0, 0, 0], [1 / 3, 2 / 3, 0], [0, 0, 0.5], [2 / 3, 1 / 3, 0.5]],
    )


def nacl_structure() -> Structure:
    return Structure.from_spacegroup(
        "Fm-3m",
        Lattice.cubic(5.64),
        ["Na", "Cl"],
        [[0, 0, 0], [0.5, 0.5, 0.5]],
    )


def srtio3_structure() -> Structure:
    return Structure.from_spacegroup(
        "Pm-3m",
        Lattice.cubic(3.905),
        ["Sr", "Ti", "O"],
        [[0, 0, 0], [0.5, 0.5, 0.5], [0.5, 0.5, 0]],
    )


def test_sns2_observation_reports_two_dimensional_bonded_connectivity():
    observation = observe_structure_dimensionality(sns2_structure())

    assert observation.status == OBSERVED
    assert observation.dimensionality == 2
    assert observation.method.id == METHOD_ID
    assert observation.method.bonding.endswith("CrystalNN.get_bonded_structure")
    assert observation.method.dimensionality.endswith("get_dimensionality_larsen")
    assert observation.reason is None


def test_si_observation_reports_three_dimensional_bonded_connectivity():
    observation = observe_structure_dimensionality(si_structure())

    assert observation.status == OBSERVED
    assert observation.dimensionality == 3
    assert observation.dimensionality != 2


@pytest.mark.parametrize(
    ("factory", "expected_dimensionality"),
    [
        (bi2se3_structure, 2),
        (mos2_structure, 2),
        (graphite_structure, 2),
        (hbn_structure, 2),
        (nacl_structure, 3),
        (srtio3_structure, 3),
    ],
)
def test_validated_control_structures_remain_sensible(factory, expected_dimensionality):
    observation = observe_structure_dimensionality(factory())

    assert observation.status == OBSERVED
    assert observation.dimensionality == expected_dimensionality


def test_sns2_component_evidence_is_preserved_without_layer_overinterpretation():
    observation = observe_structure_dimensionality(sns2_structure())

    assert len(observation.components) == 1
    component = observation.components[0]
    assert component.formula == "SnS2"
    assert component.dimensionality == 2
    assert component.orientation == (0, 0, 1)
    assert sorted(component.site_ids) == [0, 1, 2]

    payload = observation.to_dict()
    assert payload["components"][0] == {
        "dimensionality": 2,
        "formula": "SnS2",
        "orientation": [0, 0, 1],
        "site_ids": [0, 1, 2],
    }
    assert "layered" not in _payload_keys(payload)
    assert "layers" not in _payload_keys(payload)


def test_observation_payload_is_json_safe_and_deterministic():
    structure = sns2_structure()

    first = observe_structure_dimensionality(structure).to_dict()
    second = observe_structure_dimensionality(structure).to_dict()

    assert json.loads(json.dumps(first, sort_keys=True)) == first
    assert first == second


def test_observation_does_not_modify_input_structure():
    structure = sns2_structure()
    before = structure.as_dict()

    observe_structure_dimensionality(structure)

    assert structure.as_dict() == before


def test_analysis_failure_is_explicit_and_never_guessed(monkeypatch):
    class BrokenCrystalNN:
        def get_bonded_structure(self, structure):
            raise RuntimeError("crystal graph failed")

    monkeypatch.setattr(structure_dimensionality, "CrystalNN", lambda: BrokenCrystalNN())

    observation = observe_structure_dimensionality(si_structure())

    assert isinstance(observation, StructureDimensionalityObservation)
    assert observation.status == ANALYSIS_FAILED
    assert observation.dimensionality is None
    assert observation.components == ()
    assert observation.reason == "RuntimeError: crystal graph failed"
    json.dumps(observation.to_dict(), sort_keys=True)


def test_observation_does_not_change_workflow_or_generated_inputs():
    structure = si_structure()
    workflow = WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE)])
    before_workflow = workflow.to_dict()
    before_preview = preview_generated_inputs(
        structure,
        workflow,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )

    observe_structure_dimensionality(structure)

    after_preview = preview_generated_inputs(
        structure,
        workflow,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )
    assert workflow.to_dict() == before_workflow
    assert after_preview == before_preview


def test_dimensionality_helper_has_no_runtime_io_or_execution_coupling(monkeypatch):
    source = inspect.getsource(structure_dimensionality)
    forbidden_terms = (
        "paramiko",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "slurm",
        "custodian",
        "vasp",
        "Poscar",
        "CifParser",
        "open(",
        "Path(",
    )
    for term in forbidden_terms:
        assert term not in source

    def fail_open(*args, **kwargs):
        raise AssertionError("filesystem access is not part of dimensionality observation")

    monkeypatch.setattr(builtins, "open", fail_open)

    observation = observe_structure_dimensionality(si_structure())

    assert observation.status == OBSERVED


def _payload_keys(value):
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        for item in value.values():
            keys.update(_payload_keys(item))
        return keys
    if isinstance(value, list):
        keys = set()
        for item in value:
            keys.update(_payload_keys(item))
        return keys
    return set()
