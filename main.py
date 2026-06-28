from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates

from backend.parser import parse_structure
from backend.summary import summarize_structure

app = FastAPI()

templates = Jinja2Templates(directory="templates")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
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
        name="results.html",
        context={"summary": summary},
    )
