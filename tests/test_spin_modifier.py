from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.workflows import (
    apply_spin_settings,
    build_atomate2_flow_for_spec,
    incar_relax,
    incar_static,
)


assert apply_spin_settings({}, spin_polarized=False) == {
    "ISPIN": 1,
    "MAGMOM": None,
}
assert apply_spin_settings({"ENCUT": 520}, spin_polarized=False) == {
    "ENCUT": 520,
    "ISPIN": 1,
    "MAGMOM": None,
}
assert apply_spin_settings({}, spin_polarized=True) == {
    "ISPIN": 2,
}
assert apply_spin_settings({"ISPIN": 1}, spin_polarized=True) == {
    "ISPIN": 2,
}
assert apply_spin_settings(
    {"MAGMOM": {"Fe": 5}},
    spin_polarized=True,
) == {
    "MAGMOM": {"Fe": 5},
    "ISPIN": 2,
}

non_spin_static = incar_static(apply_spin_settings({}, spin_polarized=False))
assert non_spin_static["ISPIN"] == 1
assert non_spin_static["MAGMOM"] is None

spin_static = incar_static(apply_spin_settings({}, spin_polarized=True))
assert spin_static["ISPIN"] == 2
assert "MAGMOM" not in spin_static

non_spin_relax = incar_relax(apply_spin_settings({}, spin_polarized=False))
assert non_spin_relax["ISPIN"] == 1
assert non_spin_relax["MAGMOM"] is None

spin_relax = incar_relax(apply_spin_settings({}, spin_polarized=True))
assert spin_relax["ISPIN"] == 2
assert "MAGMOM" not in spin_relax


import backend.workflows as workflows

calls = []
original_build_static_flow = workflows.build_static_flow
original_build_relax_flow = workflows.build_relax_flow


def fake_build_static_flow(structure, **kwargs):
    calls.append(("static", structure, kwargs))
    return {"flow": "static", "kwargs": kwargs}


def fake_build_relax_flow(structure, **kwargs):
    calls.append(("relax", structure, kwargs))
    return {"flow": "relax", "kwargs": kwargs}


workflows.build_static_flow = fake_build_static_flow
workflows.build_relax_flow = fake_build_relax_flow
try:
    build_atomate2_flow_for_spec(
        "structure",
        CalculationSpec(Purpose.STATIC, Theory.PBE),
    )
    build_atomate2_flow_for_spec(
        "structure",
        CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.SPIN_POLARIZED}),
    )
    build_atomate2_flow_for_spec(
        "structure",
        CalculationSpec(Purpose.RELAX, Theory.PBE),
    )
    build_atomate2_flow_for_spec(
        "structure",
        CalculationSpec(Purpose.RELAX, Theory.PBE, {Modifier.SPIN_POLARIZED}),
    )
finally:
    workflows.build_static_flow = original_build_static_flow
    workflows.build_relax_flow = original_build_relax_flow

assert [call[2]["spin_polarized"] for call in calls] == [
    False,
    True,
    False,
    True,
]

print("spin modifier smoke test passed")
