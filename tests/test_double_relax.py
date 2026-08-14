import sys
from types import ModuleType, SimpleNamespace

from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.workflows import build_atomate2_flow_for_spec


class FinalStructure:
    def __init__(self, source_name):
        self.source_name = source_name


class FakeJob:
    def __init__(self, name, input_structure):
        self.name = name
        self.input_structure = input_structure
        self.output = SimpleNamespace(structure=FinalStructure(name))


class FakeRelaxMaker:
    jobs = []

    def __init__(self, *, input_set_generator, name, **kwargs):
        self.input_set_generator = input_set_generator
        self.name = name

    def make(self, structure):
        job = FakeJob(self.name, structure)
        self.jobs.append(job)
        return job


class FakeFlow:
    def __init__(self, jobs, name):
        self.jobs = jobs
        self.name = name


def install_fake_atomate2_modules():
    module_names = (
        "atomate2",
        "atomate2.vasp",
        "atomate2.vasp.jobs",
        "atomate2.vasp.jobs.core",
        "jobflow",
    )
    saved_modules = {name: sys.modules.get(name) for name in module_names}

    for name in module_names:
        sys.modules[name] = ModuleType(name)

    sys.modules["atomate2.vasp.jobs.core"].RelaxMaker = FakeRelaxMaker
    sys.modules["jobflow"].Flow = FakeFlow

    return saved_modules


def restore_modules(saved_modules):
    for name, module in saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


import backend.workflows as workflows

saved_modules = install_fake_atomate2_modules()
original_generator = workflows.build_relax_input_set_generator
generator_calls = []
FakeRelaxMaker.jobs = []


def fake_relax_input_set_generator(structure, **kwargs):
    generator_calls.append((structure, kwargs))
    return {"structure": structure, "kwargs": kwargs}


workflows.build_relax_input_set_generator = fake_relax_input_set_generator
try:
    flow = build_atomate2_flow_for_spec(
        "initial-structure",
        CalculationSpec(Purpose.DOUBLE_RELAX, Theory.PBE, {Modifier.SPIN_POLARIZED}),
        label="demo",
        resources={"ntasks": 24},
        incar={"ENCUT": 580},
        kpoints=None,
        potcar_functional="PBE_64",
    )
finally:
    workflows.build_relax_input_set_generator = original_generator
    restore_modules(saved_modules)

assert flow.name == "demo_double_relax"
assert [job.name for job in flow.jobs] == ["relax_01", "relax_02"]
assert flow.bmd_stage_directories == ("relax_01", "relax_02")
assert len(generator_calls) == 2

first_job, second_job = flow.jobs
assert first_job.input_structure == "initial-structure"
assert second_job.input_structure is first_job.output.structure
assert generator_calls[0][0] == "initial-structure"
assert generator_calls[1][0] is first_job.output.structure

for _, kwargs in generator_calls:
    assert kwargs["spin_polarized"] is True
    assert kwargs["modifiers"] == frozenset({Modifier.SPIN_POLARIZED})
    assert kwargs["resources"] == {"ntasks": 24}
    assert kwargs["incar"] == {"ENCUT": 580}
    assert kwargs["kpoints"] is None
    assert kwargs["potcar_functional"] == "PBE_64"

print("double relax smoke test passed")
