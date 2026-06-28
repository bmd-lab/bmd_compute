def _rounded_lattice_parameters(structure):
    lattice = structure.lattice

    return {
        "a": round(lattice.a, 4),
        "b": round(lattice.b, 4),
        "c": round(lattice.c, 4),
    }


def _rounded_lattice_angles(structure):
    lattice = structure.lattice

    return {
        "alpha": round(lattice.alpha, 2),
        "beta": round(lattice.beta, 2),
        "gamma": round(lattice.gamma, 2),
    }


def _symmetry_summary(structure):
    try:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

        analyzer = SpacegroupAnalyzer(structure)

        return {
            "space_group_symbol": analyzer.get_space_group_symbol(),
            "space_group_number": analyzer.get_space_group_number(),
            "crystal_system": analyzer.get_crystal_system(),
        }
    except Exception:
        return {
            "space_group_symbol": None,
            "space_group_number": None,
            "crystal_system": "Unknown",
        }


def summarize_structure(structure):
    """
    Return a template-friendly summary of a pymatgen Structure.

    FastAPI should call this function rather than reaching into pymatgen
    objects directly.
    """

    summary = {
        "formula": structure.formula,
        "reduced_formula": structure.composition.reduced_formula,
        "natoms": len(structure),
        "volume": round(structure.volume, 3),
        "density": round(structure.density, 3),
        "lattice": _rounded_lattice_parameters(structure),
        "angles": _rounded_lattice_angles(structure),
    }

    summary.update(_symmetry_summary(structure))

    return summary
