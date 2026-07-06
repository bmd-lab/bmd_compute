from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pymatgen.core import Structure


def parse_structure(text: str, fmt: str = "poscar") -> "Structure":
    """
    Parse a structure from text.

    Parameters
    ----------
    text
        POSCAR or CIF text.

    fmt
        "poscar" or "cif"

    Returns
    -------
    pymatgen.Structure
    """

    text = text.strip()

    from pymatgen.core import Structure

    return Structure.from_str(text, fmt=fmt)


def structure_from_spec(structure_spec: dict) -> "Structure":
    """
    Reconstruct a pymatgen Structure from the serialized structure portion of a
    SubmissionSpec.
    """

    from pymatgen.core import Lattice, Structure

    kind = structure_spec.get("type")
    if kind == "path":
        return Structure.from_file(structure_spec["path"])

    if kind == "pasted_text":
        fmt = structure_spec.get("format") or "poscar"
        return parse_structure(structure_spec["text"], fmt=fmt)

    if kind == "builder":
        import numpy as np

        lattice_spec = structure_spec.get("lattice", {}) or {}
        lattice = Lattice.from_parameters(
            float(lattice_spec.get("a", 3.84)),
            float(lattice_spec.get("b", 3.84)),
            float(lattice_spec.get("c", 3.84)),
            float(lattice_spec.get("alpha", 120.0)),
            float(lattice_spec.get("beta", 90.0)),
            float(lattice_spec.get("gamma", 60.0)),
        )
        coords = structure_spec["coords"]
        if structure_spec.get("coord_kind", "frac") == "cart":
            inv = np.linalg.inv(lattice.matrix.T)
            coords = [list(inv.dot(np.array(coord, float))) for coord in coords]
        return Structure(lattice, structure_spec["species"], coords)

    if kind == "mp":
        from mp_api.client import MPRester

        key = os.environ.get("MP_API_KEY")
        if not key:
            raise RuntimeError("MP_API_KEY not set")

        with MPRester(key) as mpr:
            result = mpr.materials.summary.search(material_ids=[structure_spec["query"]])
            if not result:
                raise RuntimeError(f"MP-ID not found: {structure_spec['query']}")
            structure = result[0].structure

        if structure_spec.get("conventional", True):
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

            structure = SpacegroupAnalyzer(
                structure,
                symprec=1e-3,
            ).get_conventional_standard_structure(international_monoclinic=True)
        if structure_spec.get("symmetrize", False):
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

            structure = SpacegroupAnalyzer(structure, symprec=1e-3).get_refined_structure()
        return structure

    raise RuntimeError(f"Unsupported structure spec: {structure_spec}")


__all__ = [
    "parse_structure",
    "structure_from_spec",
]
