ENCUT_STATIC_PREP_DEFAULT = 520
ENCUT_RELAX_DEFAULT = 580
ENCUT_STATIC_FINAL_DEFAULT = 620

from backend.calculations.models import CalculationSpec, Modifier, Purpose
from backend.calculations.registry import (
    calculation_spec_from_flow_spec,
    calculation_spec_from_legacy,
    validate_calculation_spec,
)


def _is_hse_incar(settings):
    if not isinstance(settings, dict):
        return False

    value = settings.get("LHFCALC")
    if isinstance(value, str) and value.strip().strip(".").upper() in ("T", "TRUE", "YES"):
        return True
    if value is True:
        return True

    for key in ("AEXX", "HFSCREEN", "ALDAC", "LHFCALC_HYBRID"):
        if key in settings:
            return True

    gga = settings.get("GGA")
    if isinstance(gga, str) and "HSE" in gga.upper():
        return True

    return False


def incar_static(settings, allow_ncore=True):
    user_settings = dict(settings or {})

    for key in ("GGA", "ENAUG", "LMIXTAU"):
        user_settings.setdefault(key, None)

    user_settings.setdefault("ISMEAR", -5)
    if user_settings.get("ISMEAR", -5) == -5:
        user_settings.setdefault("SIGMA", None)

    user_settings.setdefault("EDIFF", 1e-6)
    user_settings.setdefault("ALGO", "Normal")

    for key, value in {
        "NEDOS": 3001,
        "LORBIT": 11,
        "LVTOT": True,
        "LAECHG": True,
        "LCHARG": True,
        "LWAVE": True,
        "LELF": True,
    }.items():
        user_settings.setdefault(key, value)

    if allow_ncore and not _is_hse_incar(user_settings):
        user_settings.setdefault("NCORE", 2)

    return user_settings


def incar_relax(settings, user=None):
    """
    INCAR for relax steps, preserving the reference notebook defaults.
    """

    user_settings = dict(settings or {})
    explicit_user_settings = dict(user or {})

    if "LCHARG" not in explicit_user_settings:
        user_settings["LCHARG"] = False
    if "LWAVE" not in explicit_user_settings:
        user_settings["LWAVE"] = False

    for key in ("LAECHG", "LVTOT", "LELF", "LVHAR", "LORBIT"):
        if key not in explicit_user_settings:
            user_settings[key] = None

    for key in ("GGA", "ENAUG", "LMIXTAU"):
        user_settings.setdefault(key, None)

    user_settings.setdefault("ALGO", "Fast")
    user_settings.setdefault("ADDGRID", True)
    user_settings.setdefault("EDIFFG", -0.01)

    if _is_hse_incar(user_settings) or _is_hse_incar(explicit_user_settings):
        user_settings.setdefault("PRECFOCK", "Fast")
        user_settings.setdefault("ALGO", "Damped")

    if not _is_hse_incar(user_settings) and not _is_hse_incar(explicit_user_settings):
        user_settings.setdefault("NCORE", 2)

    return user_settings


def ksettings(structure, kpoints_config):
    if not kpoints_config:
        return None

    mode = kpoints_config.get("mode")
    value = kpoints_config.get("value")

    if mode == "mesh":
        from pymatgen.io.vasp.inputs import Kpoints

        nx, ny, nz = (int(value[0]), int(value[1]), int(value[2]))
        natoms = len(structure)

        def mesh_for(kppa):
            kpoints = Kpoints.automatic_density(structure, int(max(1, kppa)))
            if kpoints.kpts:
                mesh = kpoints.kpts[0]
                if isinstance(mesh, (list, tuple)) and len(mesh) >= 3:
                    return (int(mesh[0]), int(mesh[1]), int(mesh[2]))
            return (0, 0, 0)

        target = (nx, ny, nz)
        kppa = max(1, nx * ny * nz * max(1, natoms))
        seen = set()
        found = None

        for _ in range(64):
            mesh = mesh_for(kppa)
            if mesh == target:
                found = kppa
                break
            if mesh in seen:
                break

            seen.add(mesh)
            target_product = nx * ny * nz
            mesh_product = max(1, mesh[0] * mesh[1] * mesh[2])
            scale = max(
                target_product / mesh_product,
                nx / max(1, mesh[0]),
                ny / max(1, mesh[1]),
                nz / max(1, mesh[2]),
            )
            kppa = int(max(1, kppa * (1.25 if scale < 1 else min(3.0, 1.15 * scale))))

        if found is None:
            kppa_start = max(1, nx * ny * nz * max(1, natoms))
            for factor in [1, 1.3, 1.6, 2, 3, 4, 6, 8, 12]:
                mesh = mesh_for(int(kppa_start * factor))
                if mesh[0] >= nx and mesh[1] >= ny and mesh[2] >= nz:
                    found = int(kppa_start * factor)
                    break

        return {"grid_density": float(found if found is not None else kppa)}

    return {mode: float(value)}


def build_relax_flow(
    structure,
    *,
    label="vasp_run",
    name=".",
    isif=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import RelaxMaker
    from atomate2.vasp.sets.core import RelaxSetGenerator
    from jobflow import Flow

    user_incar = dict(incar or {})
    user_incar.setdefault("ENCUT", ENCUT_RELAX_DEFAULT)
    user_incar.setdefault("ISPIN", 2)
    user_incar.setdefault("EDIFF", 1e-6)
    user_incar.setdefault("ADDGRID", True)
    user_incar.setdefault("EDIFFG", -0.01)
    if isif is not None:
        user_incar["ISIF"] = isif

    generator = RelaxSetGenerator(
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=ksettings(structure, kpoints),
        user_incar_settings=incar_relax(user_incar, user=incar),
    )
    maker = RelaxMaker(
        input_set_generator=generator,
        name=("relax_ions" if isif == 2 else "relax"),
    )

    step_name = name.replace("00_", "").replace("01_", "").replace("02_", "").strip("./")
    flow_name = label if name in (".", "") else label + "_" + step_name

    return Flow([maker.make(structure)], name=flow_name)


def build_static_flow(
    structure,
    *,
    label="vasp_run",
    hse=False,
    prep_for_gw=False,
    intent="final",
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import StaticMaker
    from atomate2.vasp.sets.core import StaticSetGenerator
    from jobflow import Flow

    user_incar = dict(incar or {})

    if hse:
        for key, value in {"LHFCALC": True, "AEXX": 0.25, "HFSCREEN": 0.2, "ALGO": "Damped"}.items():
            user_incar.setdefault(key, value)
        for key in ("NCORE", "KPAR", "NPAR"):
            user_incar.pop(key, None)

    if intent == "prep":
        user_incar.setdefault("LWAVE", True)
        user_incar.setdefault("LCHARG", True)
        for key in ("LVTOT", "LELF", "LVHAR", "LAECHG"):
            user_incar.setdefault(key, False)
        try:
            encut_now = int(float(user_incar.get("ENCUT", 0)))
        except Exception:
            encut_now = 0
        user_incar["ENCUT"] = max(encut_now, ENCUT_STATIC_PREP_DEFAULT)
    else:
        user_incar.setdefault("LWAVE", False)
        user_incar.setdefault("LCHARG", True)
        user_incar.setdefault("ISMEAR", -5)
        user_incar.setdefault("SIGMA", 0.05)
        user_incar.setdefault("NEDOS", 4001)
        user_incar.setdefault("LORBIT", 11)
        user_incar.setdefault("LREAL", False)
        user_incar.setdefault("PREC", "Accurate")
        user_incar.setdefault("ADDGRID", True)
        try:
            encut_now = int(float(user_incar.get("ENCUT", 0)))
        except Exception:
            encut_now = 0
        user_incar["ENCUT"] = max(encut_now, ENCUT_STATIC_FINAL_DEFAULT)

    if prep_for_gw:
        user_incar["LWAVE"] = True
        user_incar["LCHARG"] = True

    generator = StaticSetGenerator(
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=ksettings(structure, kpoints),
        user_incar_settings=incar_static(user_incar, allow_ncore=not (hse or prep_for_gw)),
    )
    maker = StaticMaker(
        input_set_generator=generator,
        name=("hse_static" if hse else "static"),
    )

    return Flow([maker.make(structure)], name=label + ("_hse_static" if hse else "_static"))


def build_atomate2_flow_for_spec(
    structure,
    spec: CalculationSpec,
    *,
    label="vasp_run",
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    calculation_spec = validate_calculation_spec(spec)
    user_incar = dict(incar or {})

    if calculation_spec.purpose is Purpose.STATIC:
        return build_static_flow(
            structure,
            label=label,
            hse=_is_hse_incar(user_incar),
            intent="final",
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.RELAX:
        return build_relax_flow(
            structure,
            label=label,
            isif=2 if Modifier.IONS_ONLY in calculation_spec.modifiers else None,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    raise ValueError(
        "Only single-step Atomate2 flow construction is migrated. "
        f"Purpose '{calculation_spec.purpose.value}' requires execution or non-Atomate2 logic."
    )


def build_atomate2_flow(
    structure,
    workflow,
    *,
    label="vasp_run",
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    calculation_spec = calculation_spec_from_legacy(workflow, potcar_functional)
    from backend.calculations.builder import build_calculation_flow

    return build_calculation_flow(
        structure=structure,
        spec=calculation_spec,
        label=label,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )


def build_atomate2_flow_from_spec(structure, flow_spec: dict, *, run_name: str):
    """
    Build the Atomate2 Flow represented by a SubmissionSpec flow_spec.

    Remote execution preserves the notebook behaviour of naming the Jobflow
    Flow after the concrete run directory while reusing the same scientific
    workflow construction used by the browser preview.
    """

    calculation_spec = calculation_spec_from_flow_spec(flow_spec)
    potcar_functional = flow_spec.get("potcar_functional") or "PBE_64"
    from backend.calculations.builder import build_calculation_flow

    flow = build_calculation_flow(
        structure=structure,
        spec=calculation_spec,
        label=run_name,
        incar=flow_spec.get("incar") or flow_spec.get("incar_overrides") or {},
        kpoints=flow_spec.get("kpoints"),
        potcar_functional=potcar_functional,
    )

    if getattr(flow, "name", None) == run_name:
        return flow

    try:
        flow.name = run_name
        return flow
    except Exception:
        from jobflow import Flow

        return Flow(list(getattr(flow, "jobs", []) or []), name=run_name)


__all__ = [
    "build_atomate2_flow",
    "build_atomate2_flow_from_spec",
    "build_atomate2_flow_for_spec",
    "build_relax_flow",
    "build_static_flow",
    "incar_relax",
    "incar_static",
    "ksettings",
]
