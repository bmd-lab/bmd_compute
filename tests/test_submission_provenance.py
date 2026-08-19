import json
import re

import backend.provenance as provenance
from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.parser import parse_structure
from backend.submission import create_submission_spec, remote_preparation_file_groups


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


def _flow_spec(workflow_spec: WorkflowSpec | None = None) -> dict:
    workflow = workflow_spec or WorkflowSpec([StageSpec(StageType.STATIC, Theory.PBE)])
    return {
        "workflow": "custom_workflow",
        "workflow_spec": workflow.to_dict(),
        "potcar_functional": "PBE_64",
        "kpoints": None,
        "incar": {},
        "structure": {
            "type": "pasted_text",
            "format": "poscar",
            "text": POSCAR,
        },
    }


def _submission_spec(workflow_spec: WorkflowSpec | None = None, **kwargs) -> dict:
    return create_submission_spec(
        _flow_spec(workflow_spec),
        structure=parse_structure(POSCAR),
        label="Si provenance",
        timestamp="20260818-120000",
        ntasks=24,
        mem_gb=128,
        walltime="24:00:00",
        env={},
        **kwargs,
    )


def test_submission_provenance_block_is_json_safe_and_versioned():
    spec = _submission_spec()
    payload = json.dumps(spec["provenance"], sort_keys=True, allow_nan=False)
    restored = json.loads(payload)

    assert restored["schema_version"] == provenance.PROVENANCE_SCHEMA_VERSION
    assert restored["execution"]["workflow_spec"] == spec["flow_spec"]["workflow_spec"]
    assert restored["execution"]["resources"]["ntasks"] == 24
    assert restored["execution"]["resources"]["mem_gb"] == 128
    assert restored["execution"]["cluster"]["partition"] == spec["cluster"]["partition"]
    assert restored["execution"]["cluster"]["account"] == spec["cluster"]["account"]


def test_git_source_metadata_is_explicit():
    provenance.source_metadata.cache_clear()
    source = provenance.source_metadata()

    assert source["state"] in {"clean", "dirty", "unknown", "unavailable"}
    assert "dirty" in source
    if source["status"] == "available":
        assert re.fullmatch(r"[0-9a-f]{40}", source["git_commit"])
        assert isinstance(source["dirty"], (bool, type(None)))
    else:
        assert source["git_commit"] is None


def test_missing_git_metadata_degrades_gracefully(monkeypatch):
    provenance.source_metadata.cache_clear()
    monkeypatch.setattr(provenance, "_git_stdout", lambda *args: None)

    source = provenance.source_metadata()

    assert source["status"] == "unavailable"
    assert source["state"] == "unavailable"
    assert source["git_commit"] is None
    assert source["dirty"] is None
    provenance.source_metadata.cache_clear()


def test_scientific_package_versions_are_json_safe_strings():
    spec = _submission_spec()
    packages = spec["provenance"]["python_environment"]["preparation"]["packages"]

    for package_name in provenance.SCIENTIFIC_PACKAGE_NAMES:
        assert package_name in packages
        assert isinstance(packages[package_name], str)
        assert packages[package_name]


def test_mixed_soc_stage_vasp_executables_are_stage_local():
    workflow = WorkflowSpec([
        StageSpec(StageType.STATIC, Theory.PBE),
        StageSpec(StageType.STATIC, Theory.PBE, {Modifier.SOC}),
    ])
    spec = _submission_spec(workflow)

    stages = spec["provenance"]["vasp"]["stages"]

    assert [stage["executable"] for stage in stages] == ["vasp_std", "vasp_ncl"]
    assert "vasp_std" in stages[0]["command_template"]
    assert "vasp_ncl" in stages[1]["command_template"]
    assert stages[1]["modifiers"] == ["soc"]


def test_potcar_identity_is_recorded_without_raw_potcar_hashes():
    spec = _submission_spec()
    potcar = spec["provenance"]["potcar"]

    assert potcar["functional"] == "PBE_64"
    assert potcar["symbols"]
    assert potcar["species"]
    assert potcar["hashes"]["status"] == "not_recorded"
    assert "TITEL  =" not in json.dumps(potcar)
    assert "PAW_PBE" not in json.dumps(potcar)


def test_provenance_does_not_record_secrets_or_private_key_contents():
    spec = _submission_spec(mp_api_key="super-secret-materials-project-token")
    payload = json.dumps(spec, sort_keys=True)

    assert "super-secret-materials-project-token" not in payload
    assert "private key" not in payload.lower()
    assert "BEGIN OPENSSH PRIVATE KEY" not in payload


def test_remote_execution_provenance_is_marked_deferred_at_submission_time():
    spec = _submission_spec()
    remote = spec["provenance"]["python_environment"]["remote_execution"]
    vasp_version = spec["provenance"]["vasp"]["version"]
    module_info = spec["provenance"]["execution"]["modules"]["loaded_module_information"]

    assert remote["status"] == "deferred_until_execution"
    assert remote["python"] == spec["runner"]["python"]
    assert vasp_version["status"] == "deferred_until_execution"
    assert module_info["status"] == "deferred_until_execution"


def test_submission_json_serialization_preserves_provenance():
    spec = _submission_spec()
    groups = remote_preparation_file_groups(spec)
    submission_json = None
    for group in groups:
        for item in group.get("files", []):
            if item["path"].endswith("/submission.json"):
                submission_json = json.loads(item["text"])

    assert submission_json is not None
    assert submission_json["provenance"] == spec["provenance"]
    assert submission_json["provenance"]["schema_version"] == provenance.PROVENANCE_SCHEMA_VERSION
