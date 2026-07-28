from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates

from backend.calculations.builder import build_calculation_flow
from backend.calculations.models import CalculationSpec, Purpose, Theory
from backend.calculations.registry import (
    calculation_display_name,
    calculation_form_options,
    calculation_spec_from_legacy,
    legacy_potcar_functional_from_spec,
    legacy_workflow_from_spec,
    modifier_display_name,
    theory_display_name,
)
from backend.generated_inputs import preview_generated_inputs
from backend.monitoring import monitor_job
from backend.parser import StructureValidationError, parse_structure
from backend.remote_preparation import prepare_remote_submission, remembered_successful_preparation
from backend.remote_submission import remembered_successful_submission, submit_remote_workflow
from backend.results import load_results_for_completed_job
from backend.submission import create_submission_spec
from backend.summary import summarize_structure
from backend.workflow_summary import summarize_workflow

app = FastAPI()

templates = Jinja2Templates(directory="templates")


def page_context(
    *,
    structure_text: str = "",
    fmt: str = "poscar",
    summary=None,
    selected_spec: CalculationSpec | None = None,
    calculation_summary=None,
    generated_inputs=None,
    submission_spec=None,
    remote_preparation=None,
    submission_result=None,
    monitoring_result=None,
    results_summary=None,
    resume_job_id: str = "",
    structure_error=None,
):
    selected_spec = selected_spec or default_calculation_spec()
    return {
        "structure_text": structure_text,
        "fmt": fmt,
        "summary": summary,
        "selected_calculation": selected_calculation_context(selected_spec),
        "calculation_options": calculation_form_options(),
        "calculation": calculation_summary,
        "workflow": calculation_summary,
        "generated_inputs": generated_inputs,
        "submission_spec": submission_spec,
        "remote_preparation": remote_preparation,
        "submission_result": submission_result,
        "monitoring_result": monitoring_result,
        "results_summary": results_summary,
        "resume_job_id": resume_job_id,
        "structure_error": structure_error,
    }


def structure_error_context(exc: StructureValidationError) -> dict:
    return {
        "message": exc.message,
        "suggestion": exc.suggestion,
    }


def structure_error_response(
    request: Request,
    *,
    structure_text: str,
    fmt: str,
    exc: StructureValidationError,
    selected_spec: CalculationSpec | None = None,
):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure_text,
            fmt=fmt,
            selected_spec=selected_spec,
            structure_error=structure_error_context(exc),
        ),
        status_code=400,
    )


def default_calculation_spec() -> CalculationSpec:
    return CalculationSpec(Purpose.STATIC, Theory.PBE)


def selected_calculation_context(spec: CalculationSpec) -> dict:
    modifiers = sorted(spec.modifiers, key=lambda modifier: modifier.value)
    return {
        "purpose": spec.purpose.value,
        "theory": spec.theory.value,
        "modifiers": [modifier.value for modifier in modifiers],
        "modifier_labels": [modifier_display_name(modifier) for modifier in modifiers],
        "purpose_label": calculation_display_name(spec),
        "theory_label": theory_display_name(spec.theory),
    }


def calculation_spec_from_form(
    *,
    purpose: str | None = None,
    theory: str | None = None,
    modifiers: list[str] | None = None,
    workflow: str | None = None,
    method: str | None = None,
) -> CalculationSpec:
    if purpose or theory or modifiers:
        return CalculationSpec(
            purpose=purpose or Purpose.STATIC,
            theory=theory or Theory.PBE,
            modifiers=modifiers or (),
        )

    return calculation_spec_from_legacy(workflow, method)


def build_submission_state(
    *,
    structure_text: str,
    fmt: str,
    calculation_spec: CalculationSpec,
    timestamp: str | None = None,
):
    structure_obj = parse_structure(structure_text, fmt)
    summary = summarize_structure(structure_obj)
    potcar_functional = legacy_potcar_functional_from_spec(calculation_spec)
    legacy_workflow = legacy_workflow_from_spec(calculation_spec)
    flow = build_calculation_flow(
        structure=structure_obj,
        spec=calculation_spec,
        potcar_functional=potcar_functional,
    )
    generated_inputs = preview_generated_inputs(
        structure_obj,
        calculation_spec,
        potcar_functional=potcar_functional,
    )
    calculation_summary = summarize_workflow(flow, calculation_spec)
    flow_spec = {
        "calculation_spec": calculation_spec.to_dict(),
        "workflow": legacy_workflow,
        "potcar_functional": potcar_functional,
        "kpoints": None,
        "incar": {},
        "structure": {
            "type": "pasted_text",
            "format": fmt,
            "text": structure_text,
        },
    }
    submission_spec = create_submission_spec(
        flow_spec,
        structure=structure_obj,
        label=calculation_summary["flow_name"],
        timestamp=timestamp,
    )
    return summary, calculation_summary, generated_inputs, submission_spec


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(),
    )


@app.post("/analyze")
def analyze(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...)
):
    try:
        structure_obj = parse_structure(structure, fmt)
    except StructureValidationError as exc:
        return structure_error_response(
            request,
            structure_text=structure,
            fmt=fmt,
            exc=exc,
        )

    summary = summarize_structure(structure_obj)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
        ),
    )


@app.post("/resume")
def resume_existing_calculation(
    request: Request,
    job_id: str = Form(...),
):
    print("ENTER /resume")
    print("after parsing the form")
    print("before calling monitor_job()")
    monitoring_result = monitor_job(job_id)
    print("immediately after monitor_job() returns")
    print("before calling the results layer")
    results_summary = load_results_for_completed_job(monitoring_result)
    print("immediately after the results layer returns")
    print("immediately before returning the template response")

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            monitoring_result=monitoring_result,
            results_summary=results_summary,
            resume_job_id=job_id,
        ),
    )


@app.post("/build-calculation")
@app.post("/build-workflow")
def build_workflow(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    purpose: str | None = Form(None),
    theory: str | None = Form(None),
    modifiers: list[str] | None = Form(None),
    workflow: str | None = Form(None),
    method: str | None = Form(None),
):
    calculation_spec = calculation_spec_from_form(
        purpose=purpose,
        theory=theory,
        modifiers=modifiers,
        workflow=workflow,
        method=method,
    )
    try:
        summary, calculation_summary, generated_inputs, submission_spec = build_submission_state(
            structure_text=structure,
            fmt=fmt,
            calculation_spec=calculation_spec,
        )
    except StructureValidationError as exc:
        return structure_error_response(
            request,
            structure_text=structure,
            fmt=fmt,
            selected_spec=calculation_spec,
            exc=exc,
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_spec=calculation_spec,
            calculation_summary=calculation_summary,
            generated_inputs=generated_inputs,
            submission_spec=submission_spec,
        ),
    )


@app.post("/prepare-remote")
def prepare_remote(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    purpose: str | None = Form(None),
    theory: str | None = Form(None),
    modifiers: list[str] | None = Form(None),
    created_at: str = Form(...),
    workflow: str | None = Form(None),
    method: str | None = Form(None),
):
    calculation_spec = calculation_spec_from_form(
        purpose=purpose,
        theory=theory,
        modifiers=modifiers,
        workflow=workflow,
        method=method,
    )
    try:
        summary, calculation_summary, generated_inputs, submission_spec = build_submission_state(
            structure_text=structure,
            fmt=fmt,
            calculation_spec=calculation_spec,
            timestamp=created_at,
        )
    except StructureValidationError as exc:
        return structure_error_response(
            request,
            structure_text=structure,
            fmt=fmt,
            selected_spec=calculation_spec,
            exc=exc,
        )
    remote_preparation = prepare_remote_submission(submission_spec)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_spec=calculation_spec,
            calculation_summary=calculation_summary,
            generated_inputs=generated_inputs,
            submission_spec=submission_spec,
            remote_preparation=remote_preparation,
        ),
    )


@app.post("/submit")
def submit_workflow(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    purpose: str | None = Form(None),
    theory: str | None = Form(None),
    modifiers: list[str] | None = Form(None),
    created_at: str = Form(...),
    remote_prepared: str = Form("false"),
    workflow: str | None = Form(None),
    method: str | None = Form(None),
):
    calculation_spec = calculation_spec_from_form(
        purpose=purpose,
        theory=theory,
        modifiers=modifiers,
        workflow=workflow,
        method=method,
    )
    try:
        summary, calculation_summary, generated_inputs, submission_spec = build_submission_state(
            structure_text=structure,
            fmt=fmt,
            calculation_spec=calculation_spec,
            timestamp=created_at,
        )
    except StructureValidationError as exc:
        return structure_error_response(
            request,
            structure_text=structure,
            fmt=fmt,
            selected_spec=calculation_spec,
            exc=exc,
        )
    prepared = remote_prepared.lower() == "true"
    submission_result = submit_remote_workflow(
        submission_spec,
        remote_prepared=prepared,
    )
    remote_preparation = (
        remembered_successful_preparation(submission_spec)
        if prepared
        else None
    )
    monitoring_result = None
    if submission_result.get("status") == "success" and submission_result.get("job_id"):
        monitoring_result = monitor_job(
            submission_result["job_id"],
            submission_spec=submission_spec,
        )
    results_summary = load_results_for_completed_job(
        monitoring_result,
        submission_spec=submission_spec,
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_spec=calculation_spec,
            calculation_summary=calculation_summary,
            generated_inputs=generated_inputs,
            submission_spec=submission_spec,
            remote_preparation=remote_preparation,
            submission_result=submission_result,
            monitoring_result=monitoring_result,
            results_summary=results_summary,
        ),
    )


@app.post("/monitor")
def refresh_monitoring(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    purpose: str | None = Form(None),
    theory: str | None = Form(None),
    modifiers: list[str] | None = Form(None),
    created_at: str = Form(...),
    job_id: str = Form(...),
    submitted_at: str = Form(""),
    workflow: str | None = Form(None),
    method: str | None = Form(None),
):
    calculation_spec = calculation_spec_from_form(
        purpose=purpose,
        theory=theory,
        modifiers=modifiers,
        workflow=workflow,
        method=method,
    )
    try:
        summary, calculation_summary, generated_inputs, submission_spec = build_submission_state(
            structure_text=structure,
            fmt=fmt,
            calculation_spec=calculation_spec,
            timestamp=created_at,
        )
    except StructureValidationError as exc:
        return structure_error_response(
            request,
            structure_text=structure,
            fmt=fmt,
            selected_spec=calculation_spec,
            exc=exc,
        )
    remote_preparation = remembered_successful_preparation(submission_spec)
    submission_result = remembered_successful_submission(
        submission_spec,
        job_id,
        submitted_at=submitted_at,
    )
    monitoring_result = monitor_job(job_id, submission_spec=submission_spec)
    results_summary = load_results_for_completed_job(
        monitoring_result,
        submission_spec=submission_spec,
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_spec=calculation_spec,
            calculation_summary=calculation_summary,
            generated_inputs=generated_inputs,
            submission_spec=submission_spec,
            remote_preparation=remote_preparation,
            submission_result=submission_result,
            monitoring_result=monitoring_result,
            results_summary=results_summary,
        ),
    )
