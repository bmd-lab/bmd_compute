from fastapi import FastAPI, Request
from pydantic import BaseModel 
from fastapi.templating import Jinja2Templates

app = FastAPI()

templates = Jinja2Templates(directory="templates")

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "message": "Welcome to the Burton Materials Design Lab!"
        },
    )

@app.get("/about")
def about():
    return {
        "lab": "Burton Materials Design Lab",
        "university": "Tel Aviv University",
        "purpose": "Computational materials design"
    }

@app.get("/hello/{name}")
def hello(name: str):
    return {
        "name": name,
        "length": len(name),
        "uppercase": name.upper()
    }

class Material(BaseModel):
    formula: str


@app.post("/material")
def material(material: Material):
    return {
        "formula": material.formula,
        "message": f"You submitted {material.formula}"
    }