from __future__ import annotations

import json

import pytest

from backend.calculations.capabilities import build_capability_payload
from backend.calculations.dispersion import (
    VAN_DER_WAALS_INCAR_EFFECT,
    VAN_DER_WAALS_VDW_METHOD,
    dispersion_method_options,
    dispersion_option_payload,
)
from backend.calculations.models import CalculationSpec, Modifier, Purpose, StageSpec, StageType, Theory, WorkflowSpec
from backend.calculations.registry import (
    CalculationValidationError,
    calculation_spec_from_workflow_spec,
    validate_calculation_spec,
    validate_workflow_spec,
)
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from backend.submission import create_submission_spec
from backend.workflows import (
    build_relax_input_set_generator,
    build_static_input_set_generator,
    dispersion_method_for_stage,
)


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

FE2O3_POSCAR = """Fe2O3
5.04
1.0 0.0 0.0
-0.5 0.8660254 0.0
0.0 0.0 2.7281746
Fe O
2 3
direct
0.0 0.0 0.355
0.0 0.0 0.645
0.305 0.0 0.25
0.0 0.305 0.25
0.695 0.695 0.25
"""


def _si_structure():
    return parse_structure(SI_POSCAR)


def _workflow(stage_type, *, modifiers=None, options=None, theory=Theory.PBE):
    selected_modifiers = {Modifier.VAN_DER_WAALS} if modifiers is None else set(modifiers)
    return WorkflowSpec([
        StageSpec(
            stage_type,
            theory,
            selected_modifiers,
            options=options or {},
        )
    ])


def _preview_incar(workflow, structure=None):
    return preview_generated_inputs(
        structure or _si_structure(),
        workflow,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )["incar"]


def test_van_der_waals_modifier_is_single_current_policy():
    assert Modifier.from_value("van der Waals correction") is Modifier.VAN_DER_WAALS
    assert VAN_DER_WAALS_VDW_METHOD == "dftd3-bj"
    assert VAN_DER_WAALS_INCAR_EFFECT == {"IVDW": 12}

    policy = build_capability_payload(include_provenance=False)["modifier_policies"][0]
    assert policy["modifier"] == "van_der_waals"
    assert policy["label"] == "van der Waals correction"
    assert policy["incar_effect"] == {"IVDW": 12}
    assert policy["upstream_interface"] == {
        "atomate2_pymatgen_generator_keyword": "vdw",
        "keyword_value": "dftd3-bj",
    }
    assert "methods" not in policy


def test_pbe_static_without_vdw_omits_ivdw():
    incar = _preview_incar(_workflow(StageType.STATIC, modifiers=set()))

    assert "IVDW" not in incar


def test_pbe_static_and_relax_with_vdw_generate_ivdw_12():
    static_incar = _preview_incar(_workflow(StageType.STATIC))
    relax_incar = _preview_incar(_workflow(StageType.RELAX))

    assert "IVDW = 12" in static_incar
    assert "IVDW = 12" in relax_incar
    assert "IVDW = 11" not in static_incar
    assert "IVDW = 11" not in relax_incar


def test_pbe_relax_static_and_double_relax_apply_consistent_vdw():
    relax_static = WorkflowSpec([
        StageSpec(StageType.RELAX, Theory.PBE, {Modifier.VAN_DER_WAALS}),
        StageSpec(StageType.STATIC, Theory.PBE, {Modifier.VAN_DER_WAALS}),
    ])
    double_relax = WorkflowSpec([
        StageSpec(StageType.RELAX, Theory.PBE, {Modifier.VAN_DER_WAALS}),
        StageSpec(StageType.RELAX, Theory.PBE, {Modifier.VAN_DER_WAALS}),
    ])

    assert validate_workflow_spec(relax_static) == relax_static
    assert validate_workflow_spec(double_relax) == double_relax
    assert _preview_incar(relax_static).count("IVDW = 12") == 2
    assert _preview_incar(double_relax).count("IVDW = 12") == 2


def test_vdw_composes_with_allowed_spin_dft_u_and_gamma_only():
    spin_gamma_incar = _preview_incar(
        _workflow(
            StageType.STATIC,
            modifiers={Modifier.VAN_DER_WAALS, Modifier.SPIN_POLARIZED, Modifier.GAMMA_ONLY},
        )
    )
    assert "IVDW = 12" in spin_gamma_incar
    assert "ISPIN = 2" in spin_gamma_incar

    fe2o3_incar = _preview_incar(
        _workflow(StageType.STATIC, modifiers={Modifier.VAN_DER_WAALS, Modifier.DFT_U}),
        structure=parse_structure(FE2O3_POSCAR),
    )
    assert "IVDW = 12" in fe2o3_incar
    assert "LDAU = True" in fe2o3_incar
    assert "LDAUU = 5.3 0" in fe2o3_incar


def test_unsupported_vdw_combinations_fail_explicitly():
    unsupported_workflows = [
        WorkflowSpec([StageSpec(StageType.STATIC, Theory.HSE06, {Modifier.VAN_DER_WAALS})]),
        WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE, {Modifier.VAN_DER_WAALS, Modifier.SOC})]),
        WorkflowSpec([StageSpec(StageType.DOS, Theory.PBE, {Modifier.VAN_DER_WAALS})]),
        WorkflowSpec([StageSpec(StageType.BAND_STRUCTURE, Theory.PBE, {Modifier.VAN_DER_WAALS})]),
        WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE, options=dispersion_option_payload("dftd3"))]),
        WorkflowSpec([
            StageSpec(
                StageType.STATIC,
                Theory.PBE,
                {Modifier.VAN_DER_WAALS},
                options=dispersion_option_payload("dftd3"),
            )
        ]),
    ]

    for workflow in unsupported_workflows:
        with pytest.raises(CalculationValidationError) as excinfo:
            validate_workflow_spec(workflow)
        assert "Waals" in excinfo.value.message or "D3 dispersion" in excinfo.value.message

    with pytest.raises(CalculationValidationError) as excinfo:
        validate_calculation_spec(CalculationSpec(Purpose.STATIC, Theory.HSE06, {Modifier.VAN_DER_WAALS}))
    assert "van der Waals correction" in excinfo.value.message

    with pytest.raises(CalculationValidationError) as excinfo:
        validate_calculation_spec(CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.VAN_DER_WAALS, Modifier.SOC}))
    assert "van der Waals correction" in excinfo.value.message

    with pytest.raises(CalculationValidationError) as excinfo:
        validate_calculation_spec(CalculationSpec(Purpose.DOS, Theory.PBE, {Modifier.VAN_DER_WAALS}))
    assert "van der Waals correction" in excinfo.value.message

    with pytest.raises(CalculationValidationError) as excinfo:
        validate_calculation_spec(CalculationSpec(Purpose.BAND_STRUCTURE, Theory.PBE, {Modifier.VAN_DER_WAALS}))
    assert "van der Waals correction" in excinfo.value.message


def test_connected_relax_static_stages_must_use_same_vdw_policy():
    missing_on_static = WorkflowSpec([
        StageSpec(StageType.RELAX, Theory.PBE, {Modifier.VAN_DER_WAALS}),
        StageSpec(StageType.STATIC, Theory.PBE),
    ])
    with pytest.raises(CalculationValidationError) as excinfo:
        validate_workflow_spec(missing_on_static)
    assert "same van der Waals correction policy" in excinfo.value.message


def test_current_vdw_spec_round_trips_through_submission_provenance_without_method_options():
    workflow = _workflow(StageType.STATIC)
    compatible_spec = calculation_spec_from_workflow_spec(workflow)
    assert compatible_spec == CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.VAN_DER_WAALS})

    submission = create_submission_spec(
        {
            "workflow_spec": workflow.to_dict(),
            "potcar_functional": "PBE_64",
            "kpoints": None,
            "incar": {},
            "structure": {"type": "pasted_text", "format": "poscar", "text": SI_POSCAR},
        },
        structure=_si_structure(),
        label="Si vdw",
        timestamp="20260825-120000",
        ntasks=24,
        mem_gb=128,
        walltime="24:00:00",
        env={},
    )

    workflow_dict = workflow.to_dict()
    assert workflow_dict["stages"][0]["modifiers"] == ["van_der_waals"]
    assert workflow_dict["stages"][0]["options"] == {}
    assert submission["flow_spec"]["workflow_spec"] == workflow_dict
    assert submission["flow_spec"]["calculation_spec"] == compatible_spec.to_dict()
    assert submission["provenance"]["execution"]["workflow_spec"] == workflow_dict
    assert submission["provenance"]["execution"]["stage_order"][0]["options"] == {}
    assert submission["provenance"]["vasp"]["stages"][0]["options"] == {}
    json.dumps(submission["provenance"], sort_keys=True)


def test_preview_and_runtime_generators_agree_on_vdw_policy():
    structure = _si_structure()
    stage = StageSpec(StageType.STATIC, Theory.PBE, {Modifier.VAN_DER_WAALS})
    workflow = WorkflowSpec([stage])
    preview_incar = _preview_incar(workflow, structure=structure)

    generator = build_static_input_set_generator(
        structure,
        modifiers=stage.modifiers,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
        dispersion_method=dispersion_method_for_stage(stage),
    )
    runtime_incar = str(generator.get_input_set(structure, potcar_spec=True).incar)

    assert generator.vdw == "dftd3-bj"
    assert "IVDW = 12" in preview_incar
    assert "IVDW = 12" in runtime_incar


def test_relax_and_static_generators_use_upstream_vdw_keyword():
    structure = _si_structure()
    modifiers = {Modifier.VAN_DER_WAALS}

    relax_generator = build_relax_input_set_generator(
        structure,
        modifiers=modifiers,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )
    static_generator = build_static_input_set_generator(
        structure,
        modifiers=modifiers,
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )
    no_vdw_generator = build_static_input_set_generator(
        structure,
        modifiers=set(),
        resources={"ntasks": 24},
        potcar_functional="PBE_64",
    )

    assert relax_generator.vdw == "dftd3-bj"
    assert static_generator.vdw == "dftd3-bj"
    assert no_vdw_generator.vdw is None


def test_legacy_serialized_dispersion_remains_readable_and_truthful():
    legacy_d3_workflow = WorkflowSpec.from_dict({
        "stages": [
            {
                "stage_type": "static",
                "theory": "pbe",
                "modifiers": ["dispersion"],
                "label": None,
                "options": dispersion_option_payload("dftd3"),
            }
        ],
        "label": None,
        "recipe": None,
    })
    legacy_d3bj_workflow = WorkflowSpec.from_dict({
        "stages": [
            {
                "stage_type": "static",
                "theory": "pbe",
                "modifiers": ["dispersion"],
                "label": None,
                "options": dispersion_option_payload("dftd3-bj"),
            }
        ],
        "label": None,
        "recipe": None,
    })

    assert validate_workflow_spec(legacy_d3_workflow) == legacy_d3_workflow
    assert validate_workflow_spec(legacy_d3bj_workflow) == legacy_d3bj_workflow
    assert legacy_d3_workflow.to_dict()["stages"][0]["modifiers"] == ["dispersion"]
    assert calculation_spec_from_workflow_spec(legacy_d3_workflow) is None
    assert "IVDW = 11" in _preview_incar(legacy_d3_workflow)
    assert "IVDW = 12" not in _preview_incar(legacy_d3_workflow)
    assert "IVDW = 12" in _preview_incar(legacy_d3bj_workflow)
    assert list(dispersion_method_options()) == [
        {"value": "dftd3", "label": "DFT-D3"},
        {"value": "dftd3-bj", "label": "DFT-D3(BJ)"},
    ]


def test_vdw_policy_is_exposed_in_capability_contract_with_legacy_metadata():
    payload = build_capability_payload(include_provenance=False)
    policies = payload["modifier_policies"]
    assert len(policies) == 1
    policy = policies[0]

    assert policy["modifier"] == "van_der_waals"
    assert policy["label"] == "van der Waals correction"
    assert policy["description"] == "Adds the DFT-D3 dispersion correction with Becke-Johnson damping."
    assert policy["incar_effect"] == {"IVDW": 12}
    assert policy["phase_1_support"] == {
        "theories": ["pbe"],
        "stage_types": ["relax", "static"],
        "blocked_with_modifiers": ["soc"],
        "blocked_terminal_stage_types": ["dos", "band_structure"],
    }
    assert policy["legacy_serialized_modifier"] == {
        "modifier": "dispersion",
        "option_key": "dispersion",
        "method_key": "method",
        "methods": [
            {"value": "dftd3", "label": "DFT-D3", "incar_effect": {"IVDW": 11}},
            {"value": "dftd3-bj", "label": "DFT-D3(BJ)", "incar_effect": {"IVDW": 12}},
        ],
        "new_workflows_emit": False,
    }
