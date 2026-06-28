from backend.parser import parse_structure
from backend.workflows import build_atomate2_flow

poscar = """Si
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

structure = parse_structure(poscar)

flow = build_atomate2_flow(
    structure=structure,
    workflow="relax",
)

print(type(flow))
print()
print(flow)

print()
print(f"Number of jobs: {len(flow.jobs)}")
