from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates

from backend.parser import parse_structure

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

    context = {
        "formula": structure_obj.formula,
        "reduced_formula": structure_obj.composition.reduced_formula,
        "natoms": len(structure_obj),
        "volume": round(structure_obj.volume, 3),
    }

    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context=context,
    )
