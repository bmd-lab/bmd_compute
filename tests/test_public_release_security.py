import json
import shlex
from copy import deepcopy

import pytest
from starlette.requests import Request

import main
from backend.calculations.models import StageSpec, StageType, Theory, WorkflowSpec
from backend.config import DEFAULT_PARTITION
from backend.parser import parse_structure
from backend.submission import (
    build_sbatch_script,
    create_submission_spec,
    remote_preparation_file_groups,
    verify_submission_identity_token,
)


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

ADVERSARIAL_VALUES = [
    "20260925-120000\necho injected",
    "20260925-120000\rcontrol",
    "$(touch /tmp/bmd-injected)",
    "`touch /tmp/bmd-injected`",
    "value; touch /tmp/bmd-injected",
    "value && touch /tmp/bmd-injected",
    "value || touch /tmp/bmd-injected",
    "'\"quoted",
    "../path\\escape",
    "..",
    "x" * 5000,
]


def _flow_spec():
    workflow = WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE)])
    return {
        "workflow": "static",
        "workflow_spec": workflow.to_dict(),
        "potcar_functional": "PBE_64",
        "kpoints": None,
        "incar": {},
        "structure": {"type": "pasted_text", "format": "poscar", "text": POSCAR},
    }


def _submission_spec():
    return create_submission_spec(
        _flow_spec(),
        structure=parse_structure(POSCAR),
        label="Si static",
        timestamp="20260925-120000",
        env={},
    )


@pytest.mark.parametrize("payload", ADVERSARIAL_VALUES)
def test_noncanonical_run_timestamp_is_rejected_without_rendering_shell(payload):
    with pytest.raises(ValueError, match="timestamp"):
        create_submission_spec(
            _flow_spec(),
            structure=parse_structure(POSCAR),
            label="Si static",
            timestamp=payload,
            env={},
        )


def test_submission_identity_token_is_tamper_evident_and_bounded():
    spec = _submission_spec()
    token = spec["submission"]["identity_token"]
    identity = verify_submission_identity_token(
        token,
        expected_attempt_id=spec["submission"]["attempt_id"],
    )

    assert identity == {
        "attempt_id": spec["submission"]["attempt_id"],
        "run_timestamp": "20260925-120000",
    }
    encoded, signature = token.split(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    with pytest.raises(ValueError, match="signature"):
        verify_submission_identity_token(f"{encoded}.{replacement}{signature[1:]}")
    with pytest.raises(ValueError, match="malformed"):
        verify_submission_identity_token("x" * 5000)


@pytest.mark.parametrize("payload", ADVERSARIAL_VALUES)
def test_browser_created_at_cannot_control_prepared_submission(monkeypatch, payload):
    initial = _submission_spec()
    captured = {}

    def fake_prepare(spec, **kwargs):
        captured["spec"] = spec
        return main.remembered_successful_preparation(spec)

    monkeypatch.setattr(main, "prepare_remote_submission", fake_prepare)
    response = main.prepare_remote(
        Request({"type": "http", "method": "POST", "path": "/prepare-remote", "headers": []}),
        structure=POSCAR,
        fmt="poscar",
        purpose="static",
        theory="pbe",
        modifiers=None,
        cpus="24",
        memory_gb="128",
        walltime="72:00:00",
        queue=DEFAULT_PARTITION,
        created_at=payload,
        submission_attempt_id=initial["submission"]["attempt_id"],
        submission_identity_token=initial["submission"]["identity_token"],
        workflow_spec_json=json.dumps(_flow_spec()["workflow_spec"]),
    )

    prepared = captured["spec"]
    script = build_sbatch_script(prepared)
    assert response.status_code == 200
    assert prepared["created_at"] == "20260925-120000"
    assert prepared["run_name"].endswith("-20260925-120000")
    assert payload not in script
    assert payload not in prepared["paths"]["run_dir"]


def test_browser_created_at_cannot_control_submitted_submission(monkeypatch):
    initial = _submission_spec()
    captured = {}

    def fake_submit(spec, **kwargs):
        captured["spec"] = spec
        return main.remembered_successful_submission(spec, "123456")

    monkeypatch.setattr(main, "submit_remote_workflow", fake_submit)
    response = main.submit_workflow(
        Request({"type": "http", "method": "POST", "path": "/submit", "headers": []}),
        structure=POSCAR,
        fmt="poscar",
        purpose="static",
        theory="pbe",
        modifiers=None,
        cpus="24",
        memory_gb="128",
        walltime="72:00:00",
        queue=DEFAULT_PARTITION,
        created_at="20260925-120000\necho injected",
        submission_attempt_id=initial["submission"]["attempt_id"],
        submission_identity_token=initial["submission"]["identity_token"],
        remote_prepared="true",
        workflow_spec_json=json.dumps(_flow_spec()["workflow_spec"]),
    )

    submitted = captured["spec"]
    assert response.status_code == 200
    assert submitted["created_at"] == "20260925-120000"
    assert "echo injected" not in build_sbatch_script(submitted)


@pytest.mark.parametrize("payload", ADVERSARIAL_VALUES)
@pytest.mark.parametrize(
    ("section", "field"),
    [
        (None, "run_name"),
        ("cluster", "partition"),
        ("cluster", "account"),
        ("resources", "walltime"),
        ("paths", "slurm_out"),
        ("runner", "python"),
        ("runner", "script_name"),
        ("runner", "submission_spec_name"),
        ("runner", "backend_package_dir"),
        ("runner", "backend_init_name"),
        ("runner", "execution_module_name"),
    ],
)
def test_exact_sbatch_builder_rejects_unsafe_dynamic_fields(section, field, payload):
    spec = deepcopy(_submission_spec())
    target = spec if section is None else spec[section]
    target[field] = payload
    with pytest.raises(ValueError, match="Unsafe|invalid|allowed"):
        build_sbatch_script(spec)


@pytest.mark.parametrize("payload", ADVERSARIAL_VALUES)
def test_shell_environment_values_are_safely_quoted(payload):
    spec = deepcopy(_submission_spec())
    spec["environment"]["JOBFLOW_CONFIG_FILE"] = payload

    script = build_sbatch_script(spec)

    assert f"export JOBFLOW_CONFIG_FILE={shlex.quote(payload)}" in script


def test_exact_displayed_script_remains_exact_uploaded_sftp_payload():
    spec = _submission_spec()
    displayed = build_sbatch_script(spec)
    uploaded = next(
        item["text"]
        for group in remote_preparation_file_groups(spec)
        if group["step"] == "Submission script written"
        for item in group["files"]
    )
    assert displayed == uploaded
