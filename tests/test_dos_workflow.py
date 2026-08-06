import sys
from types import ModuleType, SimpleNamespace

from backend.calculations.models import CalculationSpec, Modifier, Purpose, Theory
from backend.workflow_summary import summarize_workflow
from backend.workflows import build_atomate2_flow_for_spec


class OutputValue:
    def __init__(self, stage_name):
        self.stage_name = stage_name


class FakeJob:
    def __init__(self, name, input_structure, prev_dir=None, mode=None):
        self.name = name
        self.input_structure = input_structure
        self.prev_dir = prev_dir
        self.mode = mode
        self.output = SimpleNamespace(
            structure=OutputValue(f"{name}:structure"),
            dir_name=OutputValue(f"{name}:dir"),
        )


class FakeMaker:
    def __init__(self, *, input_set_generator, name):
        self.input_set_generator = input_set_generator
        self.name = name

    def make(self, structure, prev_dir=None, mode=None):
        return FakeJob(self.name, structure, prev_dir=prev_dir, mode=mode)


class FakeFlow:
    def __init__(self, jobs, name, metadata=None):
        self.jobs = jobs
        self.name = name
        self.metadata = metadata or {}


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

    jobs_core = sys.modules["atomate2.vasp.jobs.core"]
    jobs_core.RelaxMaker = FakeMaker
    jobs_core.StaticMaker = FakeMaker
    jobs_core.NonSCFMaker = FakeMaker
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
original_relax_generator = workflows.build_relax_input_set_generator
original_static_generator = workflows.build_static_input_set_generator
original_dos_generator = workflows.build_dos_input_set_generator
generator_calls = []


def fake_generator(kind):
    def build(structure, **kwargs):
        generator_calls.append((kind, structure, kwargs))
        return {"kind": kind, "structure": structure, "kwargs": kwargs}

    return build


workflows.build_relax_input_set_generator = fake_generator("relax")
workflows.build_static_input_set_generator = fake_generator("static")
workflows.build_dos_input_set_generator = fake_generator("dos")
try:
    flow = build_atomate2_flow_for_spec(
        "initial-structure",
        CalculationSpec(Purpose.DOS, Theory.PBE, {Modifier.SPIN_POLARIZED}),
        label="demo",
        resources={"ntasks": 24},
        incar={"ENCUT": 580},
        kpoints={"grid_density": 1000},
        potcar_functional="PBE_64",
    )
finally:
    workflows.build_relax_input_set_generator = original_relax_generator
    workflows.build_static_input_set_generator = original_static_generator
    workflows.build_dos_input_set_generator = original_dos_generator
    restore_modules(saved_modules)

assert flow.name == "demo_dos"
assert [job.name for job in flow.jobs] == ["stage_01", "stage_02", "stage_03"]
assert flow.metadata["bmd_stage_directories"] == ("stage_01", "stage_02", "stage_03")

relax_job, static_job, dos_job = flow.jobs
assert relax_job.input_structure == "initial-structure"
assert relax_job.prev_dir is None
assert static_job.input_structure is relax_job.output.structure
assert static_job.prev_dir is relax_job.output.dir_name
assert dos_job.input_structure is static_job.output.structure
assert dos_job.prev_dir is static_job.output.dir_name
assert dos_job.mode == "uniform"

assert [call[0] for call in generator_calls] == ["relax", "static", "dos"]
assert generator_calls[0][1] == "initial-structure"
assert generator_calls[1][1] is relax_job.output.structure
assert generator_calls[2][1] is static_job.output.structure

for _, _, kwargs in generator_calls:
    assert kwargs["spin_polarized"] is True
    assert kwargs["modifiers"] == frozenset({Modifier.SPIN_POLARIZED})
    assert kwargs["resources"] == {"ntasks": 24}
    assert kwargs["incar"] == {"ENCUT": 580}
    assert kwargs["kpoints"] == {"grid_density": 1000}
    assert kwargs["potcar_functional"] == "PBE_64"

summary = summarize_workflow(flow, CalculationSpec(Purpose.DOS, Theory.PBE))
assert summary["calculation_type"] == "Density of States"
assert summary["calculation_plan"] == [
    "Geometry Optimisation",
    "Static Energy",
    "Density of States",
]

print("dos workflow smoke test passed")
