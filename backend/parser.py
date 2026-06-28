from pymatgen.core import Structure


def parse_structure(text: str, fmt: str = "poscar") -> Structure:
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

    return Structure.from_str(text, fmt=fmt)