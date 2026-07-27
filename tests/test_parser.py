from backend.parser import (
    INVALID_POSCAR_ERROR,
    POSCAR_ELEMENT_ERROR,
    StructureValidationError,
    parse_structure,
)


valid_poscar = """Si
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

missing_element_line_poscar = """Si
5.43
0.0 0.5 0.5
0.5 0.0 0.5
0.5 0.5 0.0
2
direct
0.0 0.0 0.0
0.25 0.25 0.25
"""

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

structure = parse_structure(valid_poscar)
assert structure.formula == "Si2"
assert round(structure.lattice.a, 6) == 3.83959

try:
    parse_structure(missing_element_line_poscar)
except StructureValidationError as exc:
    assert exc.message == POSCAR_ELEMENT_ERROR
    assert "element-symbol line" in (exc.suggestion or "")
else:
    raise AssertionError("POSCAR missing an element-symbol line should be rejected.")

try:
    parse_structure(malformed_poscar)
except StructureValidationError as exc:
    assert exc.message == INVALID_POSCAR_ERROR
    assert exc.suggestion is None
else:
    raise AssertionError("Malformed POSCAR should be rejected as invalid.")

print("parser smoke test passed")
