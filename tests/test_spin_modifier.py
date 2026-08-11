from backend.calculations.models import CalculationSpec, Modifier, Purpose, StageType, Theory
from backend.calculations.resources import (
    ALLOWED_CPU_COUNTS,
    ExecutionResources,
    ncore_for_execution_resources,
    stage_allows_automatic_ncore,
)
from backend.calculations.registry import CalculationValidationError
from backend.workflows import (
    apply_modifier_incar_settings,
    apply_dft_u_settings,
    apply_resource_incar_settings,
    apply_stage_resource_incar_settings,
    apply_spin_settings,
    build_atomate2_flow_for_spec,
    incar_relax,
    incar_static,
    ksettings_for_modifiers,
    validate_input_set_for_modifiers,
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
assert apply_modifier_incar_settings(
    {},
    modifiers={Modifier.SPIN_POLARIZED},
)["ISPIN"] == 2
non_dft_u_settings = apply_modifier_incar_settings(
    {
        "LDAU": True,
        "LDAUTYPE": 2,
        "LDAUL": {"Ni": 2, "O": -1},
        "LDAUU": {"Ni": 6.2, "O": 0},
        "LDAUJ": {"Ni": 0, "O": 0},
        "LDAUPRINT": 1,
        "LMAXMIX": 4,
    },
    modifiers=(),
)
assert non_dft_u_settings["ISPIN"] == 1
assert non_dft_u_settings["MAGMOM"] is None
for key in ("LDAU", "LDAUTYPE", "LDAUL", "LDAUU", "LDAUJ", "LDAUPRINT", "LMAXMIX"):
    assert non_dft_u_settings[key] is None

dft_u_settings = apply_modifier_incar_settings(
    {
        "LDAU": True,
        "LDAUTYPE": 2,
        "LDAUL": {"Ni": 2, "O": -1},
        "LDAUU": {"Ni": 6.2, "O": 0},
        "LDAUJ": {"Ni": 0, "O": 0},
        "LDAUPRINT": 1,
        "LMAXMIX": 4,
    },
    modifiers={Modifier.DFT_U},
)
assert dft_u_settings["LDAU"] is True
assert dft_u_settings["LDAUU"] == {"Ni": 6.2, "O": 0}
assert apply_dft_u_settings({"LDAU": True}, dft_u=False)["LDAU"] is None
assert ALLOWED_CPU_COUNTS == (24, 48, 72, 96, 120, 144, 168, 192)
assert ncore_for_execution_resources(ExecutionResources(cpus=24)) == 8
assert ncore_for_execution_resources({"ntasks": 48}) == 8
try:
    ExecutionResources(cpus=25)
except CalculationValidationError as exc:
    assert "CPUs must be one of" in exc.message
else:
    raise AssertionError("Unsupported CPU counts should be rejected.")
assert apply_resource_incar_settings(
    {"ENCUT": 520},
    resources=ExecutionResources(cpus=24),
) == {
    "ENCUT": 520,
    "NCORE": 8,
}
assert apply_resource_incar_settings(
    {"NCORE": 4},
    resources=ExecutionResources(cpus=48),
) == {
    "NCORE": 8,
}
assert apply_resource_incar_settings(
    {"NCORE": 4},
    resources=ExecutionResources(cpus=24),
    allow_ncore=False,
) == {}
assert stage_allows_automatic_ncore(StageType.RELAX)
assert stage_allows_automatic_ncore(StageType.STATIC)
assert stage_allows_automatic_ncore(StageType.DOS)
assert not stage_allows_automatic_ncore(StageType.BAND_STRUCTURE)
assert apply_stage_resource_incar_settings(
    {"ENCUT": 520},
    stage_type=StageType.STATIC,
    resources=ExecutionResources(cpus=24),
) == {
    "ENCUT": 520,
    "NCORE": 8,
}
assert apply_stage_resource_incar_settings(
    {"ENCUT": 520},
    stage_type=StageType.BAND_STRUCTURE,
    resources=ExecutionResources(cpus=24),
) == {
    "ENCUT": 520,
}
assert apply_stage_resource_incar_settings(
    {"NCORE": 4},
    stage_type=StageType.BAND_STRUCTURE,
    resources=ExecutionResources(cpus=24),
) == {
    "NCORE": 4,
}
assert apply_modifier_incar_settings(
    {"MAGMOM": {"Fe": 5}},
    modifiers={Modifier.SOC},
) == {
    "ISPIN": 2,
    "LDAU": None,
    "LDAUTYPE": None,
    "LDAUL": None,
    "LDAUU": None,
    "LDAUJ": None,
    "LDAUPRINT": None,
    "LMAXMIX": None,
    "LSORBIT": True,
    "LNONCOLLINEAR": True,
    "ISYM": 0,
    "SAXIS": [0, 0, 1],
    "MAGMOM": None,
}
assert ksettings_for_modifiers(
    "structure",
    {"mode": "grid_density", "value": 1000},
    modifiers={Modifier.GAMMA_ONLY},
) == {"grid_density": 1.0}
assert ksettings_for_modifiers(
    "structure",
    {"mode": "grid_density", "value": 1000},
    modifiers=(),
) == {"grid_density": 1000.0}


class FakeInputSet:
    def __init__(self, incar):
        self.incar = incar


validate_input_set_for_modifiers(
    FakeInputSet({"LDAU": True, "LDAUU": [0, 5.3]}),
    spec=CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.DFT_U}),
)
try:
    validate_input_set_for_modifiers(
        FakeInputSet({"LDAU": False, "LDAUU": [0, 0]}),
        spec=CalculationSpec(Purpose.STATIC, Theory.PBE, {Modifier.DFT_U}),
    )
except CalculationValidationError as exc:
    assert "DFT+U was requested" in exc.message
else:
    raise AssertionError("DFT+U should fail when no active U values are generated.")

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
assert [call[2]["modifiers"] for call in calls] == [
    frozenset(),
    frozenset({Modifier.SPIN_POLARIZED}),
    frozenset(),
    frozenset({Modifier.SPIN_POLARIZED}),
]
assert [call[2]["resources"] for call in calls] == [None, None, None, None]

print("spin modifier smoke test passed")
