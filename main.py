from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates

from backend.parser import parse_structure
from backend.remote_preparation import prepare_remote_submission
from backend.submission import create_submission_spec
from backend.summary import summarize_structure
from backend.workflow_summary import summarize_workflow
from backend.workflows import build_atomate2_flow

app = FastAPI()

templates = Jinja2Templates(directory="templates")


def page_context(
    *,
    structure_text: str = "",
    fmt: str = "poscar",
    summary=None,
    selected_workflow: str = "static",
    selected_method: str = "PBE_64",
    workflow_summary=None,
    submission_spec=None,
    remote_preparation=None,
):
    return {
        "structure_text": structure_text,
        "fmt": fmt,
        "summary": summary,
        "selected_workflow": selected_workflow,
        "selected_method": selected_method,
        "workflow": workflow_summary,
        "submission_spec": submission_spec,
        "remote_preparation": remote_preparation,
    }


def build_submission_state(
    *,
    structure_text: str,
    fmt: str,
    workflow: str,
    method: str,
    timestamp: str | None = None,
):
    structure_obj = parse_structure(structure_text, fmt)
    summary = summarize_structure(structure_obj)
    flow = build_atomate2_flow(
        structure=structure_obj,
        workflow=workflow,
        potcar_functional=method,
    )
    workflow_summary = summarize_workflow(flow, workflow)
    flow_spec = {
        "workflow": workflow,
        "potcar_functional": method,
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
        label=workflow_summary["flow_name"],
        timestamp=timestamp,
    )
    return summary, workflow_summary, submission_spec


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
    structure_obj = parse_structure(structure, fmt)
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


@app.post("/build-workflow")
def build_workflow(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    workflow: str = Form(...),
    method: str = Form(...)
):
    summary, workflow_summary, submission_spec = build_submission_state(
        structure_text=structure,
        fmt=fmt,
        workflow=workflow,
        method=method,
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_workflow=workflow,
            selected_method=method,
            workflow_summary=workflow_summary,
            submission_spec=submission_spec,
        ),
    )


@app.post("/prepare-remote")
def prepare_remote(
    request: Request,
    structure: str = Form(...),
    fmt: str = Form(...),
    workflow: str = Form(...),
    method: str = Form(...),
    created_at: str = Form(...),
):
    summary, workflow_summary, submission_spec = build_submission_state(
        structure_text=structure,
        fmt=fmt,
        workflow=workflow,
        method=method,
        timestamp=created_at,
    )
    remote_preparation = prepare_remote_submission(submission_spec)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_context(
            structure_text=structure,
            fmt=fmt,
            summary=summary,
            selected_workflow=workflow,
            selected_method=method,
            workflow_summary=workflow_summary,
            submission_spec=submission_spec,
            remote_preparation=remote_preparation,
        ),
    )
