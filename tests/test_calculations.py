from backend.calculations.builder import build_calculation_flow
from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.calculations.registry import (
    calculation_display_name,
    calculation_form_options,
    calculation_spec_from_flow_spec,
    calculation_spec_from_legacy,
    legacy_potcar_functional_from_spec,
    legacy_workflow_from_spec,
    supported_combinations,
    validate_calculation_spec,
)


spec = CalculationSpec(
    purpose="relax",
    theory="PBE",
    modifiers={"ions-only"},
    label="compat",
)

assert spec.purpose is Purpose.RELAX
assert spec.theory is Theory.PBE
assert spec.modifiers == frozenset({Modifier.IONS_ONLY})
assert Modifier.from_value("DFT+U") is Modifier.DFT_U
assert Modifier.from_value("Spin Polarised") is Modifier.SPIN_POLARIZED
assert spec.to_dict() == {
    "purpose": "relax",
    "theory": "pbe",
    "modifiers": ["ions_only"],
    "label": "compat",
}

assert legacy_workflow_from_spec(CalculationSpec(Purpose.STATIC, Theory.PBE)) == "static"
assert legacy_workflow_from_spec(CalculationSpec(Purpose.RELAX, Theory.PBE)) == "relax"
assert legacy_workflow_from_spec(spec) == "relax_ions"
assert legacy_potcar_functional_from_spec(spec) == "PBE_64"

legacy_spec = calculation_spec_from_legacy("relax_ions", "PBE_64")
assert legacy_spec.purpose is Purpose.RELAX
assert legacy_spec.theory is Theory.PBE
assert legacy_spec.modifiers == frozenset({Modifier.IONS_ONLY})

flow_spec_spec = calculation_spec_from_flow_spec(
    {
        "calculation_spec": {
            "purpose": "relax",
            "theory": "pbe",
            "modifiers": ["ions_only"],
        },
        "workflow": "static",
        "potcar_functional": "PBE_64",
    }
)
assert flow_spec_spec == legacy_spec

assert CalculationSpec(Purpose.STATIC, Theory.PBE) in supported_combinations()
assert calculation_display_name(CalculationSpec(Purpose.RELAX, Theory.PBE)) == "Geometry Optimisation"
assert calculation_display_name(CalculationSpec(Purpose.STATIC, Theory.PBE)) == "Static Energy"

form_options = calculation_form_options()
assert [option["label"] for option in form_options["purposes"]] == [
    "Geometry Optimisation",
    "Static Energy",
]
assert [option["label"] for option in form_options["theories"]] == ["PBE"]
assert [option["label"] for option in form_options["modifiers"]] == [
    "Spin Polarised",
    "Spin-Orbit Coupling (SOC)",
    "DFT+U",
    "Gamma-only",
]
modifier_options = {option["value"]: option for option in form_options["modifiers"]}
for modifier in ("spin_polarized", "soc", "dft_u", "gamma_only"):
    assert modifier_options[modifier]["enabled"] is True
    assert modifier_options[modifier]["tooltip"] == ""

spin_static_spec = CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SPIN_POLARIZED})
spin_relax_spec = CalculationSpec(Purpose.RELAX, Theory.PBE, {Modifier.SPIN_POLARIZED})
soc_static_spec = CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SOC})
dft_u_relax_spec = CalculationSpec(Purpose.RELAX, Theory.PBE, {Modifier.DFT_U})
gamma_static_spec = CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.GAMMA_ONLY})
assert validate_calculation_spec(spin_static_spec) == spin_static_spec
assert validate_calculation_spec(spin_relax_spec) == spin_relax_spec
assert validate_calculation_spec(soc_static_spec) == soc_static_spec
assert validate_calculation_spec(dft_u_relax_spec) == dft_u_relax_spec
assert validate_calculation_spec(gamma_static_spec) == gamma_static_spec
assert legacy_workflow_from_spec(spin_static_spec) == "static"
assert legacy_workflow_from_spec(spin_relax_spec) == "relax"
assert legacy_workflow_from_spec(soc_static_spec) == "static"
assert legacy_workflow_from_spec(dft_u_relax_spec) == "relax"
assert legacy_workflow_from_spec(gamma_static_spec) == "static"

try:
    validate_calculation_spec(CalculationSpec(Purpose.DOS, Theory.PBE))
except ValueError as exc:
    assert "not implemented in the compatibility builder" in str(exc)
else:
    raise AssertionError("DOS should not be implemented in the compatibility builder yet.")

for unsupported_theory in (Theory.R2SCAN, Theory.HSE06):
    try:
        build_calculation_flow(
            structure="structure",
            spec=CalculationSpec(Purpose.STATIC, unsupported_theory),
        )
    except NotImplementedError as exc:
        assert unsupported_theory.value in str(exc)
    else:
        raise AssertionError(
            f"{unsupported_theory.value} should not be implemented yet."
        )

import backend.workflows as workflows

calls = []
original_build_atomate2_flow_for_spec = workflows.build_atomate2_flow_for_spec


def fake_build_atomate2_flow_for_spec(**kwargs):
    calls.append(kwargs)
    return {"flow": kwargs}


workflows.build_atomate2_flow_for_spec = fake_build_atomate2_flow_for_spec
try:
    flow = build_calculation_flow(
        structure="structure",
        spec=spec,
        label="demo",
        incar={"ENCUT": 520},
        kpoints={"grid_density": 1000},
        potcar_functional="PBE_64",
    )
finally:
    workflows.build_atomate2_flow_for_spec = original_build_atomate2_flow_for_spec

assert flow == {"flow": calls[0]}
assert calls == [
    {
        "structure": "structure",
        "spec": spec,
        "label": "demo",
        "incar": {"ENCUT": 520},
        "kpoints": {"grid_density": 1000},
        "potcar_functional": "PBE_64",
    }
]

print("calculation architecture smoke test passed")
