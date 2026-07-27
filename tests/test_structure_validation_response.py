from backend.parser import INVALID_POSCAR_ERROR
from main import analyze
from starlette.requests import Request


malformed_poscar = """Si
5.43
0.0 0.5 0.5
0.5 0.0 0.5
0.5 0.5 0.0
Si
2
direct
0.0 0.0 0.0
"""

request = Request(
    {
        "type": "http",
        "method": "POST",
        "path": "/analyze",
        "headers": [],
    }
)
response = analyze(request, structure=malformed_poscar, fmt="poscar")

assert response.status_code == 400
assert response.context["structure_error"]["message"] == INVALID_POSCAR_ERROR

print("structure validation response smoke test passed")
