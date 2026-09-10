from __future__ import annotations

import json

import main
from backend.calculations.dispersion import (
    DEFAULT_DISPERSION_METHOD,
    dispersion_option_payload,
)
from backend.calculations.models import Modifier, StageSpec, StageType, Theory, WorkflowSpec
from backend.generated_inputs import preview_generated_inputs
from backend.parser import parse_structure
from starlette.requests import Request


SNS2_POSCAR = """SnS2
1.0
3.648 0.0 0.0
-1.824 3.159 0.0
0.0 0.0 5.899
Sn S
1 2
direct
0.0 0.0 0.0
0.3333333333333333 0.6666666666666666 0.25
0.6666666666666666 0.3333333333333333 0.75
"""


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
        }
    )


def _render_response(response) -> str:
    return response.template.render(response.context)


def _workflow_json(stage: StageSpec) -> str:
    return json.dumps(WorkflowSpec([stage], recipe=stage.stage_type.value).to_dict())


def _build_with_stage(stage: StageSpec):
    return main.build_workflow(
        _request("/build-workflow"),
        structure=SNS2_POSCAR,
        fmt="poscar",
        purpose=None,
        theory=None,
        modifiers=None,
        cpus=None,
        memory_gb=None,
        walltime=None,
        queue=None,
        workflow_spec_json=_workflow_json(stage),
        workflow=None,
        method=None,
    )


def test_browser_dispersion_ui_has_checkbox_without_method_dropdown():
    response = main.analyze(
        _request("/analyze"),
        structure=SNS2_POSCAR,
        fmt="poscar",
    )
    html = _render_response(response)

    assert response.status_code == 200
    assert "Dispersion correction" in html
    assert "Gamma-only" not in html
    assert "gamma_only" not in html
    assert "DFT-D3" not in html
    assert "DFT-D3(BJ)" not in html
    assert "dispersion_methods" not in html
    assert "data-stage-dispersion-method" not in html
    assert "data-dispersion-control" not in html


def test_browser_style_dispersion_off_generates_no_ivdw():
    response = _build_with_stage(StageSpec(StageType.STATIC, Theory.PBE))

    assert response.status_code == 200
    assert "IVDW" not in response.context["generated_inputs"]["incar"]
    assert response.context["selected_workflow"]["stages"][0]["modifiers"] == []
    assert response.context["selected_workflow"]["stages"][0]["options"] == {}


def test_browser_style_dispersion_on_uses_backend_default_d3bj():
    response = _build_with_stage(
        StageSpec(
            StageType.STATIC,
            Theory.PBE,
            {Modifier.DISPERSION},
            options=dispersion_option_payload(),
        )
    )

    assert response.status_code == 200
    assert DEFAULT_DISPERSION_METHOD == "dftd3-bj"
    assert "IVDW = 12" in response.context["generated_inputs"]["incar"]
    stage = response.context["selected_workflow"]["stages"][0]
    assert stage["modifiers"] == ["dispersion"]
    assert stage["options"] == {"dispersion": {"method": DEFAULT_DISPERSION_METHOD}}


def test_backend_dispersion_default_and_explicit_methods_remain_supported():
    structure = parse_structure(SNS2_POSCAR)
    default_preview = preview_generated_inputs(
        structure,
        WorkflowSpec(
            [StageSpec(StageType.STATIC, Theory.PBE, {Modifier.DISPERSION})]
        ),
        potcar_functional="PBE_64",
    )
    explicit_d3_preview = preview_generated_inputs(
        structure,
        WorkflowSpec(
            [
                StageSpec(
                    StageType.STATIC,
                    Theory.PBE,
                    {Modifier.DISPERSION},
                    options=dispersion_option_payload("dftd3"),
                )
            ]
        ),
        potcar_functional="PBE_64",
    )
    explicit_d3bj_preview = preview_generated_inputs(
        structure,
        WorkflowSpec(
            [
                StageSpec(
                    StageType.STATIC,
                    Theory.PBE,
                    {Modifier.DISPERSION},
                    options=dispersion_option_payload("dftd3-bj"),
                )
            ]
        ),
        potcar_functional="PBE_64",
    )

    assert "IVDW = 12" in default_preview["incar"]
    assert "IVDW = 11" in explicit_d3_preview["incar"]
    assert "IVDW = 12" in explicit_d3bj_preview["incar"]
