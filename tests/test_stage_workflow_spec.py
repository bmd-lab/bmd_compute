from __future__ import annotations

import sys
import types
from contextlib import contextmanager

from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.calculations.registry import (
    CalculationValidationError,
    calculation_form_options,
    calculation_spec_from_workflow_spec,
    validate_workflow_spec,
    workflow_result_stage_directory,
    workflow_stage_directories,
)
from backend.generated_inputs import preview_generated_inputs
from backend.submission import create_submission_spec
from backend.workflow_results import (
    workflow_result_file_keys,
    workflow_result_parse_dos,
    workflow_result_parse_eigenvalues,
)
from backend.workflow_summary import calculation_plan_from_workflow_spec, summarize_workflow
from backend.workflows import (
    build_atomate2_flow_for_workflow_spec,
    workflow_stage_artifact_policies,
)


class FakeText:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


class FakeIncar(dict):
    def __str__(self):
        return "\n".join(
            f"{key} = {value}"
            for key, value in sorted(self.items())
            if value is not None
        )


class FakeOutput:
    def __init__(self, *, structure, dir_name):
        self.structure = structure
        self.dir_name = dir_name


class FakeJob:
    def __init__(
        self,
        *,
        name,
        structure,
        input_set_generator,
        prev_dir=None,
        mode=None,
        run_vasp_kwargs=None,
    ):
        self.name = name
        self.structure = structure
        self.input_set_generator = input_set_generator
        self.prev_dir = prev_dir
        self.mode = mode
        self.run_vasp_kwargs = run_vasp_kwargs or {}
        self.output = FakeOutput(
            structure=f"{name}_output_structure",
            dir_name=f"/remote/run/{name}",
        )


class FakeGenerator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get_input_set(self, structure, potcar_spec=True):
        assert potcar_spec is True
        return types.SimpleNamespace(
            incar=FakeIncar(self.kwargs["user_incar_settings"]),
            kpoints=FakeText("KPOINTS"),
            poscar=FakeText(f"POSCAR {structure}"),
        )


class FakeRelaxSetGenerator(FakeGenerator):
    def get_input_set(self, structure, potcar_spec=True):
        assert potcar_spec is True
        incar = {
            "IBRION": 2,
            "ISIF": 3,
            "NSW": 99,
        }
        incar.update(self.kwargs["user_incar_settings"])
        return types.SimpleNamespace(
            incar=FakeIncar(incar),
            kpoints=FakeText("KPOINTS"),
            poscar=FakeText(f"POSCAR {structure}"),
        )


class FakeMaker:
    def __init__(self, *, input_set_generator, name, run_vasp_kwargs=None):
        self.input_set_generator = input_set_generator
        self.name = name
        self.run_vasp_kwargs = run_vasp_kwargs or {}

    def make(self, structure, prev_dir=None, mode=None):
        return FakeJob(
            name=self.name,
            structure=structure,
            input_set_generator=self.input_set_generator,
            prev_dir=prev_dir,
            mode=mode,
            run_vasp_kwargs=self.run_vasp_kwargs,
        )


class FakeFlow:
    def __init__(self, jobs, name=None, metadata=None):
        self.jobs = jobs
        self.name = name
        self.metadata = metadata or {}


@contextmanager
def fake_atomate2_and_jobflow():
    modules = {
        "atomate2": types.ModuleType("atomate2"),
        "atomate2.vasp": types.ModuleType("atomate2.vasp"),
        "atomate2.vasp.jobs": types.ModuleType("atomate2.vasp.jobs"),
        "atomate2.vasp.jobs.core": types.ModuleType("atomate2.vasp.jobs.core"),
        "atomate2.vasp.sets": types.ModuleType("atomate2.vasp.sets"),
        "atomate2.vasp.sets.core": types.ModuleType("atomate2.vasp.sets.core"),
        "jobflow": types.ModuleType("jobflow"),
    }
    modules["atomate2.vasp.jobs.core"].RelaxMaker = FakeMaker
    modules["atomate2.vasp.jobs.core"].StaticMaker = FakeMaker
    modules["atomate2.vasp.jobs.core"].NonSCFMaker = FakeMaker
    modules["atomate2.vasp.sets.core"].RelaxSetGenerator = FakeRelaxSetGenerator
    modules["atomate2.vasp.sets.core"].StaticSetGenerator = FakeGenerator
    modules["atomate2.vasp.sets.core"].NonSCFSetGenerator = FakeGenerator
    modules["jobflow"].Flow = FakeFlow

    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


mixed_relax_static = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(
            StageType.STATIC,
            Theory.HSE06,
            {Modifier.SPIN_POLARIZED},
        ),
    ],
    recipe="custom",
)
validated_mixed = validate_workflow_spec(mixed_relax_static)
assert calculation_spec_from_workflow_spec(validated_mixed) is None
assert workflow_stage_directories(validated_mixed) == ("stage_01", "stage_02")
assert workflow_result_stage_directory(validated_mixed) == "stage_02"
assert calculation_plan_from_workflow_spec(validated_mixed) == [
    "Geometry Optimisation (PBE)",
    "Static Energy (HSE06)",
]

dos_workflow = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE),
        StageSpec(StageType.DOS, Theory.PBE),
    ],
    recipe="dos",
)
band_workflow = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE),
        StageSpec(StageType.BAND_STRUCTURE, Theory.PBE),
    ],
    recipe="band_structure",
)
double_relax_workflow = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(StageType.RELAX, Theory.PBE),
    ],
    recipe="double_relax",
)
assert workflow_stage_directories(double_relax_workflow) == ("relax_01", "relax_02")
assert workflow_stage_directories(dos_workflow) == ("stage_01", "stage_02", "stage_03")
assert workflow_result_file_keys(dos_workflow) == ("doscar",)
assert workflow_result_file_keys(band_workflow) == ("kpoints",)
assert workflow_result_parse_dos(dos_workflow)
assert workflow_result_parse_eigenvalues(band_workflow)

soc_static_workflow = WorkflowSpec(
    [
        StageSpec(StageType.RELAX, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE, {Modifier.SOC}),
    ],
    recipe="custom",
)
validated_soc_static = validate_workflow_spec(soc_static_workflow)
assert calculation_spec_from_workflow_spec(validated_soc_static) is None
assert workflow_stage_directories(validated_soc_static) == ("stage_01", "stage_02", "stage_03")
assert workflow_stage_artifact_policies(validated_soc_static) == (
    {"write_wavecar": False, "copy_from_previous": ()},
    {"write_wavecar": False, "copy_from_previous": ()},
    {"write_wavecar": False, "copy_from_previous": ()},
)
assert calculation_plan_from_workflow_spec(validated_soc_static) == [
    "Geometry Optimisation",
    "Static Energy",
    "Static Energy",
]

for invalid_workflow in (
    WorkflowSpec([StageSpec(StageType.RELAX, Theory.PBE, {Modifier.SOC})]),
    WorkflowSpec(
        [
            StageSpec(StageType.STATIC, Theory.PBE),
            StageSpec(StageType.DOS, Theory.PBE, {Modifier.SOC}),
        ]
    ),
    WorkflowSpec(
        [
            StageSpec(StageType.STATIC, Theory.PBE),
            StageSpec(StageType.BAND_STRUCTURE, Theory.PBE, {Modifier.SOC}),
        ]
    ),
    WorkflowSpec([StageSpec(StageType.DOS, Theory.PBE)]),
    WorkflowSpec(
        [
            StageSpec(StageType.STATIC, Theory.PBE),
            StageSpec(StageType.DOS, Theory.HSE06),
        ]
    ),
    WorkflowSpec(
        [
            StageSpec(StageType.STATIC, Theory.PBE),
            StageSpec(
                StageType.BAND_STRUCTURE,
                Theory.PBE,
                {Modifier.GAMMA_ONLY},
            ),
        ]
    ),
):
    try:
        validate_workflow_spec(invalid_workflow)
    except CalculationValidationError:
        pass
    else:
        raise AssertionError(f"{invalid_workflow} should fail validation.")

recipes = {
    recipe["value"]: WorkflowSpec.from_dict(recipe["workflow_spec"])
    for recipe in calculation_form_options()["recipes"]
}
assert recipes["dos"] == dos_workflow
assert recipes["band_structure"] == band_workflow

with fake_atomate2_and_jobflow():
    mixed_flow = build_atomate2_flow_for_workflow_spec(
        "initial_structure",
        mixed_relax_static,
        label="Si-mixed",
        resources={"ntasks": 24},
    )
    dos_flow = build_atomate2_flow_for_workflow_spec(
        "initial_structure",
        dos_workflow,
        label="Si-dos",
        resources={"ntasks": 24},
    )
    band_flow = build_atomate2_flow_for_workflow_spec(
        "initial_structure",
        band_workflow,
        label="Si-bands",
        resources={"ntasks": 24},
    )
    generated_inputs = preview_generated_inputs(
        "initial_structure",
        mixed_relax_static,
        resources={"ntasks": 24},
    )
    band_generated_inputs = preview_generated_inputs(
        "initial_structure",
        band_workflow,
        resources={"ntasks": 24},
    )
    soc_static_flow = build_atomate2_flow_for_workflow_spec(
        "initial_structure",
        soc_static_workflow,
        label="Si-soc",
        resources={"ntasks": 24},
    )
    soc_generated_inputs = preview_generated_inputs(
        "initial_structure",
        soc_static_workflow,
        resources={"ntasks": 24},
    )

assert mixed_flow.name == "Si-mixed_custom_workflow"
assert mixed_flow.metadata["bmd_stage_directories"] == ("stage_01", "stage_02")
assert [job.name for job in mixed_flow.jobs] == ["stage_01", "stage_02"]
assert mixed_flow.jobs[1].structure == mixed_flow.jobs[0].output.structure
assert mixed_flow.jobs[1].prev_dir == mixed_flow.jobs[0].output.dir_name

relax_incar = mixed_flow.jobs[0].input_set_generator.kwargs["user_incar_settings"]
static_incar = mixed_flow.jobs[1].input_set_generator.kwargs["user_incar_settings"]
assert "LHFCALC" not in relax_incar
assert "ISPIN" not in relax_incar
assert "MAGMOM" not in relax_incar
assert relax_incar["NCORE"] == 8
assert static_incar["LHFCALC"] is True
assert static_incar["PRECFOCK"] == "Accurate"
assert static_incar["ISMEAR"] == 0
assert static_incar["ISPIN"] == 2
assert static_incar["NCORE"] == relax_incar["NCORE"] == 8

assert [job.name for job in dos_flow.jobs] == ["stage_01", "stage_02", "stage_03"]
assert dos_flow.jobs[2].prev_dir == dos_flow.jobs[1].output.dir_name
assert dos_flow.jobs[2].mode == "uniform"
dos_static_incar = dos_flow.jobs[1].input_set_generator.kwargs["user_incar_settings"]
dos_incar = dos_flow.jobs[2].input_set_generator.kwargs["user_incar_settings"]
assert dos_incar["NCORE"] == dos_static_incar["NCORE"] == 8

assert [job.name for job in band_flow.jobs] == ["stage_01", "stage_02", "stage_03"]
assert band_flow.jobs[2].prev_dir == band_flow.jobs[1].output.dir_name
assert band_flow.jobs[2].mode == "line"
band_relax_incar = band_flow.jobs[0].input_set_generator.kwargs["user_incar_settings"]
band_static_incar = band_flow.jobs[1].input_set_generator.kwargs["user_incar_settings"]
band_incar = band_flow.jobs[2].input_set_generator.kwargs["user_incar_settings"]
assert band_relax_incar["NCORE"] == 8
assert band_static_incar["NCORE"] == 8
assert "ISPIN" not in band_relax_incar
assert "ISPIN" not in band_static_incar
assert "ISPIN" not in band_incar
assert "NCORE" not in band_incar

relax_section, static_section = generated_inputs["incar"].split("\n\n", 1)
assert "# Stage 1 - Geometry Optimisation (PBE)" in relax_section
assert "# Stage 2 - Static Energy (HSE06)" in static_section
assert "LHFCALC" not in relax_section
assert "PRECFOCK = Accurate" in static_section
assert "ISMEAR = 0" in static_section
assert "ISMEAR = -5" not in static_section
assert "ISPIN = 2" in static_section
assert "NCORE = 8" in static_section

band_relax_section, band_static_and_path = band_generated_inputs["incar"].split("\n\n", 1)
band_static_section, band_section = band_static_and_path.split("\n\n", 1)
assert "# Stage 1 - Geometry Optimisation (PBE)" in band_relax_section
assert "# Stage 2 - Static Energy (PBE)" in band_static_section
assert "# Stage 3 - Band Structure (PBE)" in band_section
assert "NCORE = 8" in band_relax_section
assert "NCORE = 8" in band_static_section
assert "NCORE" not in band_section

assert [job.name for job in soc_static_flow.jobs] == ["stage_01", "stage_02", "stage_03"]
assert soc_static_flow.jobs[1].prev_dir == soc_static_flow.jobs[0].output.dir_name
assert soc_static_flow.jobs[2].prev_dir == soc_static_flow.jobs[1].output.dir_name
assert soc_static_flow.metadata["bmd_stage_artifacts"] == [
    {"write_wavecar": False, "copy_from_previous": []},
    {"write_wavecar": False, "copy_from_previous": []},
    {"write_wavecar": False, "copy_from_previous": []},
]
assert soc_static_flow.jobs[0].run_vasp_kwargs == {}
assert soc_static_flow.jobs[1].run_vasp_kwargs == {}
assert "vasp_ncl" in soc_static_flow.jobs[2].run_vasp_kwargs["vasp_cmd"]
assert "vasp_std" not in soc_static_flow.jobs[2].run_vasp_kwargs["vasp_cmd"]
soc_static_incar = soc_static_flow.jobs[2].input_set_generator.kwargs["user_incar_settings"]
soc_precursor_incar = soc_static_flow.jobs[1].input_set_generator.kwargs["user_incar_settings"]
assert soc_precursor_incar["LWAVE"] is False
assert soc_static_incar["LSORBIT"] is True
assert soc_static_incar["LNONCOLLINEAR"] is True
assert soc_static_incar["ISPIN"] is None
assert soc_static_incar["GGA_COMPAT"] is False
assert soc_static_incar["LELF"] is None
assert soc_static_incar["LWAVE"] is False
assert soc_static_incar["NCORE"] == 8
soc_stage_1, soc_stage_2_and_3 = soc_generated_inputs["incar"].split("\n\n", 1)
soc_stage_2, soc_stage_3 = soc_stage_2_and_3.split("\n\n", 1)
assert "# VASP executable - vasp_std" in soc_stage_1
assert "# VASP executable - vasp_std" in soc_stage_2
assert "# VASP executable - vasp_ncl" in soc_stage_3
assert "LWAVE = False" in soc_stage_2
assert "LWAVE = False" in soc_stage_3
assert "LSORBIT = True" in soc_stage_3
assert "GGA_COMPAT = False" in soc_stage_3
assert "ISPIN" not in soc_stage_3
assert "LELF" not in soc_stage_3

summary = summarize_workflow(mixed_flow, mixed_relax_static)
assert summary["calculation_type"] == "Geometry Optimisation + Static Energy"
assert summary["theory"] == "mixed"
assert summary["theory_label"] == "Mixed"
assert summary["calculation_plan"] == [
    "Geometry Optimisation (PBE)",
    "Static Energy (HSE06)",
]

submission = create_submission_spec(
    {
        "workflow_spec": mixed_relax_static.to_dict(),
        "potcar_functional": "PBE_64",
        "kpoints": None,
        "incar": {},
        "structure": {"type": "parsed"},
    },
    label="Si mixed",
    timestamp="20260629-120000",
    env={},
)
assert "calculation_spec" not in submission["flow_spec"]
assert submission["flow_spec"]["workflow"] == "custom_workflow"
assert submission["flow_spec"]["workflow_spec"] == mixed_relax_static.to_dict()
assert submission["paths"]["stage_dirs"] == {
    "stage_01": "/bmd-db/guest/flows/Si-mixed-20260629-120000/stage_01",
    "stage_02": "/bmd-db/guest/flows/Si-mixed-20260629-120000/stage_02",
}
assert submission["paths"]["result_dir"].endswith("/stage_02")

print("stage workflow specification smoke test passed")
