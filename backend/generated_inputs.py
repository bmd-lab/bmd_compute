from __future__ import annotations

from backend.calculations.models import CalculationSpec
from backend.submission import build_slurm_preview_script
from backend.workflows import build_vasp_input_set_for_spec


def preview_generated_inputs(
    structure,
    spec: CalculationSpec,
    *,
    incar: dict | None = None,
    kpoints: dict | None = None,
    resources=None,
    potcar_functional: str = "PBE_64",
) -> dict:
    """
    Return template-friendly VASP input previews for a calculation.

    The preview uses the same input-set generator path as workflow construction,
    with POTCAR generation switched to symbol-only mode so POTCAR files are not
    read locally and no HPC resources are contacted.
    """

    input_set = build_vasp_input_set_for_spec(
        structure,
        spec,
        incar=incar,
        kpoints=kpoints,
        resources=resources,
        potcar_functional=potcar_functional,
    )

    return {
        "incar": _input_text(input_set.incar),
        "kpoints": _input_text(input_set.kpoints),
        "poscar": _input_text(input_set.poscar),
    }


def _input_text(input_object) -> str:
    return str(input_object).rstrip()


def preview_slurm_script(submission_spec: dict) -> str:
    return build_slurm_preview_script(submission_spec).replace("\r\n", "\n").replace("\r", "\n")


__all__ = [
    "preview_generated_inputs",
    "preview_slurm_script",
]
