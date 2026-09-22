import hashlib

import pytest

import backend.submission as submission
from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.calculations.resources import ExecutionResources
from backend.config import DEFAULT_ACCOUNT, DEFAULT_PARTITION, MODULES
from backend.submission import (
    build_sbatch_script,
    build_submission_script_artifact,
    remote_preparation_file_groups,
)
from backend.workflows import vasp_command_for_modifiers, vasp_executable_for_modifiers
from main import build_submission_state


POSCAR = """Si
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


def _submission_state(workflow_spec: WorkflowSpec):
    return build_submission_state(
        structure_text=POSCAR,
        fmt="poscar",
        workflow_spec=workflow_spec,
        execution_resources=ExecutionResources(
            nodes=1,
            cpus=48,
            memory_gb=256,
            walltime="12:00:00",
            queue=DEFAULT_PARTITION,
            account=DEFAULT_ACCOUNT,
        ),
        timestamp="20260922-120000",
        submission_attempt_id="12345678-1234-5678-1234-567812345678",
    )


def _uploaded_script(submission_spec: dict) -> str:
    group = next(
        group
        for group in remote_preparation_file_groups(submission_spec)
        if group["step"] == "Submission script written"
    )
    assert len(group["files"]) == 1
    return group["files"][0]["text"]


@pytest.mark.parametrize("theory", [Theory.PBE, Theory.HSE06])
def test_displayed_slurm_script_is_the_exact_uploaded_script(theory):
    workflow = WorkflowSpec([StageSpec(StageType.STATIC, theory)])
    _, _, generated_inputs, submission_spec = _submission_state(workflow)

    displayed = generated_inputs["slurm_script"]
    authoritative = build_sbatch_script(submission_spec)
    uploaded = _uploaded_script(submission_spec)

    assert displayed == authoritative == uploaded
    assert displayed.startswith("#!/usr/bin/env bash\n")
    assert f"#SBATCH -p {DEFAULT_PARTITION}" in displayed
    assert f"#SBATCH --account={DEFAULT_ACCOUNT}" in displayed
    assert "#SBATCH --time=12:00:00" in displayed
    assert "#SBATCH --nodes=1" in displayed
    assert "#SBATCH --ntasks=48" in displayed
    assert "#SBATCH --mem=256G" in displayed
    for module_name in MODULES:
        assert f"module load {module_name}\n" in displayed
    assert f"{submission_spec['runner']['python']} -u run_job.py" in displayed
    assert "\nmpirun -n $SLURM_NTASKS vasp_std\n" not in displayed


def test_submission_script_digest_covers_displayed_and_uploaded_bytes():
    workflow = WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE)])
    _, _, generated_inputs, submission_spec = _submission_state(workflow)
    displayed = generated_inputs["slurm_script"]
    uploaded = _uploaded_script(submission_spec)
    artifact = build_submission_script_artifact(submission_spec)
    provenance = submission_spec["provenance"]["execution"]["submission_script"]
    expected_digest = hashlib.sha256(displayed.encode("utf-8")).hexdigest()

    assert artifact["text"] == displayed == uploaded
    assert artifact["sha256"] == expected_digest
    assert artifact["size_bytes"] == len(displayed.encode("utf-8"))
    assert provenance == {
        "path": submission_spec["paths"]["remote_script"],
        "builder": "backend.submission.build_sbatch_script",
        "encoding": "utf-8",
        "hash_algorithm": "sha256",
        "sha256": expected_digest,
        "size_bytes": len(displayed.encode("utf-8")),
    }


def test_mixed_workflow_keeps_stage_local_executable_provenance():
    workflow = WorkflowSpec([
        StageSpec(StageType.STATIC, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE, {Modifier.SOC}),
    ])
    _, _, generated_inputs, submission_spec = _submission_state(workflow)

    stages = submission_spec["provenance"]["vasp"]["stages"]
    assert [stage["executable"] for stage in stages] == ["vasp_std", "vasp_ncl"]
    assert [row["executable"] for row in generated_inputs["vasp_executables"]] == [
        "vasp_std",
        "vasp_ncl",
    ]
    assert generated_inputs["slurm_script"] == _uploaded_script(submission_spec)


@pytest.mark.parametrize("theory", [Theory.PBE, Theory.HSE06])
def test_soc_stage_executable_routing_remains_vasp_ncl(theory):
    stage = StageSpec(StageType.STATIC, theory, {Modifier.SOC})

    assert vasp_executable_for_modifiers(stage.modifiers) == "vasp_ncl"
    assert vasp_command_for_modifiers(
        stage.modifiers,
        base_command="mpirun -n $SLURM_NTASKS vasp_std",
    ) == "mpirun -n '$SLURM_NTASKS' vasp_ncl"


def test_parallel_simplified_slurm_preview_builder_no_longer_exists():
    assert not hasattr(submission, "build_slurm_preview_script")
