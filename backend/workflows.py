ENCUT_STATIC_PREP_DEFAULT = 520
ENCUT_RELAX_DEFAULT = 580
ENCUT_STATIC_FINAL_DEFAULT = 620
BAND_STRUCTURE_LINE_DENSITY_DEFAULT = 40
DFT_U_INCAR_KEYS = (
    "LDAU",
    "LDAUTYPE",
    "LDAUL",
    "LDAUU",
    "LDAUJ",
    "LDAUPRINT",
    "LMAXMIX",
)

from backend.calculations.models import CalculationSpec, Modifier, Purpose
from backend.calculations.resources import (
    ncore_for_execution_resources,
)
from backend.calculations.registry import (
    CalculationValidationError,
    calculation_stage_directories,
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

    return user_settings


def ksettings(structure, kpoints_config):
    if not kpoints_config:
        return None

    mode = kpoints_config.get("mode")
    value = kpoints_config.get("value")

    if mode == "gamma":
        return {"grid_density": 1.0}

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


def apply_spin_settings(user_incar, *, spin_polarized: bool):
    settings = dict(user_incar or {})
    if spin_polarized:
        settings["ISPIN"] = 2
    else:
        settings["ISPIN"] = 1
        settings["MAGMOM"] = None

    return settings


def calculation_modifiers_from_options(
    *,
    modifiers=None,
    spin_polarized: bool = False,
) -> frozenset[Modifier]:
    normalized = set(modifiers or ())
    if spin_polarized:
        normalized.add(Modifier.SPIN_POLARIZED)
    return frozenset(Modifier.from_value(modifier) for modifier in normalized)


def apply_modifier_incar_settings(user_incar, *, modifiers) -> dict:
    settings = dict(user_incar or {})
    normalized_modifiers = calculation_modifiers_from_options(modifiers=modifiers)
    spin_polarized = (
        Modifier.SPIN_POLARIZED in normalized_modifiers
        or Modifier.SOC in normalized_modifiers
    )

    settings = apply_spin_settings(settings, spin_polarized=spin_polarized)
    settings = apply_dft_u_settings(
        settings,
        dft_u=Modifier.DFT_U in normalized_modifiers,
    )

    if Modifier.SOC in normalized_modifiers:
        settings["LSORBIT"] = True
        settings["LNONCOLLINEAR"] = True
        settings["ISYM"] = 0
        settings.setdefault("SAXIS", [0, 0, 1])
        settings["MAGMOM"] = None

    return settings


def apply_dft_u_settings(user_incar, *, dft_u: bool) -> dict:
    settings = dict(user_incar or {})
    if dft_u:
        return settings

    for key in DFT_U_INCAR_KEYS:
        settings[key] = None

    return settings


def apply_resource_incar_settings(user_incar, *, resources=None, allow_ncore=True) -> dict:
    settings = dict(user_incar or {})
    if not allow_ncore:
        settings.pop("NCORE", None)
        return settings

    settings["NCORE"] = ncore_for_execution_resources(resources)
    return settings


def ksettings_for_modifiers(structure, kpoints_config, *, modifiers):
    normalized_modifiers = calculation_modifiers_from_options(modifiers=modifiers)
    if Modifier.GAMMA_ONLY in normalized_modifiers:
        return ksettings(structure, {"mode": "gamma", "value": 1})

    return ksettings(structure, kpoints_config)


def line_mode_ksettings(kpoints_config=None):
    if kpoints_config:
        mode = str(kpoints_config.get("mode") or "").strip().lower()
        value = kpoints_config.get("value")
        if mode in ("line", "line_density"):
            return {"line_density": int(float(value))}

        if "line_density" in kpoints_config:
            return {"line_density": int(float(kpoints_config["line_density"]))}

    return {"line_density": BAND_STRUCTURE_LINE_DENSITY_DEFAULT}


def validate_input_set_for_modifiers(input_set, *, spec: CalculationSpec) -> None:
    if Modifier.DFT_U in spec.modifiers and not _input_set_has_active_dft_u(input_set):
        raise CalculationValidationError(
            "DFT+U was requested, but no U values are available for this structure "
            "with the current PBE input set.",
            suggestion=(
                "Remove DFT+U for this material, or add a reviewed Burton Lab "
                "override before submitting the calculation."
            ),
        )


def _input_set_has_active_dft_u(input_set) -> bool:
    incar = getattr(input_set, "incar", {}) or {}
    if not _truthy_incar_value(incar.get("LDAU")):
        return False

    return any(_numeric_values(incar.get("LDAUU")))


def _truthy_incar_value(value) -> bool:
    if isinstance(value, str):
        return value.strip().strip(".").upper() in {"T", "TRUE", "YES", "1"}
    return bool(value)


def _numeric_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _numeric_values(nested)
        return

    if isinstance(value, (list, tuple)):
        for nested in value:
            yield from _numeric_values(nested)
        return

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return

    if abs(numeric) > 1e-12:
        yield numeric


def build_relax_input_set_generator(
    structure,
    *,
    isif=None,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.sets.core import RelaxSetGenerator

    calculation_modifiers = calculation_modifiers_from_options(
        modifiers=modifiers,
        spin_polarized=spin_polarized,
    )
    user_incar = dict(incar or {})
    user_incar = apply_modifier_incar_settings(
        user_incar,
        modifiers=calculation_modifiers,
    )
    user_incar.setdefault("ENCUT", ENCUT_RELAX_DEFAULT)
    user_incar.setdefault("EDIFF", 1e-6)
    user_incar.setdefault("ADDGRID", True)
    user_incar.setdefault("EDIFFG", -0.01)
    if isif is not None:
        user_incar["ISIF"] = isif
    user_incar = apply_resource_incar_settings(
        user_incar,
        resources=resources,
        allow_ncore=not _is_hse_incar(user_incar),
    )

    return RelaxSetGenerator(
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=ksettings_for_modifiers(
            structure,
            kpoints,
            modifiers=calculation_modifiers,
        ),
        user_incar_settings=incar_relax(user_incar, user=incar),
    )


def build_relax_flow(
    structure,
    *,
    label="vasp_run",
    name=".",
    isif=None,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import RelaxMaker
    from jobflow import Flow

    generator = build_relax_input_set_generator(
        structure,
        isif=isif,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    maker = RelaxMaker(
        input_set_generator=generator,
        name=("relax_ions" if isif == 2 else "relax"),
    )

    step_name = name.replace("00_", "").replace("01_", "").replace("02_", "").strip("./")
    flow_name = label if name in (".", "") else label + "_" + step_name

    return Flow([maker.make(structure)], name=flow_name)


def build_double_relax_flow(
    structure,
    *,
    label="vasp_run",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import RelaxMaker
    from jobflow import Flow

    stage_directories = calculation_stage_directories(
        CalculationSpec(Purpose.DOUBLE_RELAX, modifiers=modifiers or ())
    )
    first_stage_dir, second_stage_dir = stage_directories

    first_generator = build_relax_input_set_generator(
        structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    first_maker = RelaxMaker(
        input_set_generator=first_generator,
        name=first_stage_dir,
    )
    first_relax = first_maker.make(structure)

    second_generator = build_relax_input_set_generator(
        first_relax.output.structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    second_maker = RelaxMaker(
        input_set_generator=second_generator,
        name=second_stage_dir,
    )
    second_relax = second_maker.make(first_relax.output.structure)

    try:
        return Flow(
            [first_relax, second_relax],
            name=f"{label}_double_relax",
            metadata={"bmd_stage_directories": stage_directories},
        )
    except TypeError:
        flow = Flow([first_relax, second_relax], name=f"{label}_double_relax")
        flow.bmd_stage_directories = stage_directories
        return flow


def _static_user_incar_settings(
    *,
    hse=False,
    prep_for_gw=False,
    intent="final",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
):
    calculation_modifiers = calculation_modifiers_from_options(
        modifiers=modifiers,
        spin_polarized=spin_polarized,
    )
    user_incar = dict(incar or {})
    user_incar = apply_modifier_incar_settings(
        user_incar,
        modifiers=calculation_modifiers,
    )

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

    user_incar = apply_resource_incar_settings(
        user_incar,
        resources=resources,
        allow_ncore=not (hse or prep_for_gw),
    )

    return incar_static(user_incar, allow_ncore=not (hse or prep_for_gw))


def _static_restart_incar_settings(
    *,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
):
    settings = _static_user_incar_settings(
        hse=_is_hse_incar(incar or {}),
        intent="final",
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
    )
    settings["ICHARG"] = 11
    return settings


def build_static_input_set_generator(
    structure,
    *,
    hse=False,
    prep_for_gw=False,
    intent="final",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.sets.core import StaticSetGenerator

    calculation_modifiers = calculation_modifiers_from_options(
        modifiers=modifiers,
        spin_polarized=spin_polarized,
    )
    user_incar_settings = _static_user_incar_settings(
        hse=hse,
        prep_for_gw=prep_for_gw,
        intent=intent,
        modifiers=calculation_modifiers,
        resources=resources,
        incar=incar,
    )

    return StaticSetGenerator(
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=ksettings_for_modifiers(
            structure,
            kpoints,
            modifiers=calculation_modifiers,
        ),
        user_incar_settings=user_incar_settings,
    )


def build_static_flow(
    structure,
    *,
    label="vasp_run",
    hse=False,
    prep_for_gw=False,
    intent="final",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import StaticMaker
    from jobflow import Flow

    generator = build_static_input_set_generator(
        structure,
        hse=hse,
        prep_for_gw=prep_for_gw,
        intent=intent,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    maker = StaticMaker(
        input_set_generator=generator,
        name=("hse_static" if hse else "static"),
    )

    return Flow([maker.make(structure)], name=label + ("_hse_static" if hse else "_static"))


def build_dos_input_set_generator(
    structure,
    *,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.sets.core import NonSCFSetGenerator

    calculation_modifiers = calculation_modifiers_from_options(
        modifiers=modifiers,
        spin_polarized=spin_polarized,
    )
    # Keep the DOS grid and basis compatible with the preceding static CHGCAR.
    dos_incar = _static_restart_incar_settings(
        spin_polarized=spin_polarized,
        modifiers=calculation_modifiers,
        resources=resources,
        incar=incar,
    )

    return NonSCFSetGenerator(
        mode="uniform",
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=ksettings_for_modifiers(
            structure,
            kpoints,
            modifiers=calculation_modifiers,
        ),
        user_incar_settings=dos_incar,
    )


def build_dos_flow(
    structure,
    *,
    label="vasp_run",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import NonSCFMaker, RelaxMaker, StaticMaker
    from jobflow import Flow

    stage_directories = calculation_stage_directories(
        CalculationSpec(Purpose.DOS, modifiers=modifiers or ())
    )
    relax_stage_dir, static_stage_dir, dos_stage_dir = stage_directories

    relax_generator = build_relax_input_set_generator(
        structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    relax_job = RelaxMaker(
        input_set_generator=relax_generator,
        name=relax_stage_dir,
    ).make(structure)

    static_generator = build_static_input_set_generator(
        relax_job.output.structure,
        hse=_is_hse_incar(incar or {}),
        intent="final",
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    static_job = StaticMaker(
        input_set_generator=static_generator,
        name=static_stage_dir,
    ).make(
        relax_job.output.structure,
        prev_dir=relax_job.output.dir_name,
    )

    dos_generator = build_dos_input_set_generator(
        static_job.output.structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    dos_job = NonSCFMaker(
        input_set_generator=dos_generator,
        name=dos_stage_dir,
    ).make(
        static_job.output.structure,
        prev_dir=static_job.output.dir_name,
        mode="uniform",
    )

    try:
        return Flow(
            [relax_job, static_job, dos_job],
            name=f"{label}_dos",
            metadata={"bmd_stage_directories": stage_directories},
        )
    except TypeError:
        flow = Flow([relax_job, static_job, dos_job], name=f"{label}_dos")
        flow.bmd_stage_directories = stage_directories
        return flow


def build_band_structure_input_set_generator(
    structure,
    *,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.sets.core import NonSCFSetGenerator

    calculation_modifiers = calculation_modifiers_from_options(
        modifiers=modifiers,
        spin_polarized=spin_polarized,
    )
    if Modifier.GAMMA_ONLY in calculation_modifiers:
        raise CalculationValidationError(
            "Gamma-only k-points are not compatible with a Band Structure calculation.",
            suggestion=(
                "Remove Gamma-only so BMD Compute can generate the required "
                "high-symmetry line-mode k-point path."
            ),
        )

    # Keep the band path grid and basis compatible with the preceding static CHGCAR.
    band_incar = _static_restart_incar_settings(
        spin_polarized=spin_polarized,
        modifiers=calculation_modifiers,
        resources=resources,
        incar=incar,
    )

    return NonSCFSetGenerator(
        mode="line",
        user_potcar_functional=potcar_functional,
        user_kpoints_settings=line_mode_ksettings(kpoints),
        user_incar_settings=band_incar,
    )


def build_band_structure_flow(
    structure,
    *,
    label="vasp_run",
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import NonSCFMaker, RelaxMaker, StaticMaker
    from jobflow import Flow

    stage_directories = calculation_stage_directories(
        CalculationSpec(Purpose.BAND_STRUCTURE, modifiers=modifiers or ())
    )
    relax_stage_dir, static_stage_dir, band_stage_dir = stage_directories

    relax_generator = build_relax_input_set_generator(
        structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    relax_job = RelaxMaker(
        input_set_generator=relax_generator,
        name=relax_stage_dir,
    ).make(structure)

    static_generator = build_static_input_set_generator(
        relax_job.output.structure,
        hse=_is_hse_incar(incar or {}),
        intent="final",
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    static_job = StaticMaker(
        input_set_generator=static_generator,
        name=static_stage_dir,
    ).make(
        relax_job.output.structure,
        prev_dir=relax_job.output.dir_name,
    )

    band_generator = build_band_structure_input_set_generator(
        static_job.output.structure,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    band_job = NonSCFMaker(
        input_set_generator=band_generator,
        name=band_stage_dir,
    ).make(
        static_job.output.structure,
        prev_dir=static_job.output.dir_name,
        mode="line",
    )

    try:
        return Flow(
            [relax_job, static_job, band_job],
            name=f"{label}_band_structure",
            metadata={"bmd_stage_directories": stage_directories},
        )
    except TypeError:
        flow = Flow([relax_job, static_job, band_job], name=f"{label}_band_structure")
        flow.bmd_stage_directories = stage_directories
        return flow


def build_vasp_input_set_generator_for_spec(
    structure,
    spec: CalculationSpec,
    *,
    incar=None,
    kpoints=None,
    resources=None,
    potcar_functional="PBE_64",
):
    calculation_spec = validate_calculation_spec(spec)
    user_incar = dict(incar or {})
    calculation_modifiers = calculation_spec.modifiers
    spin_polarized = Modifier.SPIN_POLARIZED in calculation_modifiers

    if calculation_spec.purpose is Purpose.STATIC:
        return build_static_input_set_generator(
            structure,
            hse=_is_hse_incar(user_incar),
            intent="final",
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose in (Purpose.RELAX, Purpose.DOUBLE_RELAX):
        return build_relax_input_set_generator(
            structure,
            isif=2 if Modifier.IONS_ONLY in calculation_spec.modifiers else None,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.DOS:
        return build_dos_input_set_generator(
            structure,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.BAND_STRUCTURE:
        return build_band_structure_input_set_generator(
            structure,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    raise ValueError(
        "Only single-step VASP input generation is migrated. "
        f"Purpose '{calculation_spec.purpose.value}' requires execution or non-Atomate2 logic."
    )


def build_vasp_input_set_for_spec(
    structure,
    spec: CalculationSpec,
    *,
    incar=None,
    kpoints=None,
    resources=None,
    potcar_functional="PBE_64",
):
    calculation_spec = validate_calculation_spec(spec)
    generator = build_vasp_input_set_generator_for_spec(
        structure,
        calculation_spec,
        incar=incar,
        kpoints=kpoints,
        resources=resources,
        potcar_functional=potcar_functional,
    )
    input_set = generator.get_input_set(structure, potcar_spec=True)
    validate_input_set_for_modifiers(input_set, spec=calculation_spec)
    return input_set


def build_atomate2_flow_for_spec(
    structure,
    spec: CalculationSpec,
    *,
    label="vasp_run",
    incar=None,
    kpoints=None,
    resources=None,
    potcar_functional="PBE_64",
):
    calculation_spec = validate_calculation_spec(spec)
    user_incar = dict(incar or {})
    calculation_modifiers = calculation_spec.modifiers
    spin_polarized = Modifier.SPIN_POLARIZED in calculation_modifiers

    if calculation_spec.purpose is Purpose.STATIC:
        return build_static_flow(
            structure,
            label=label,
            hse=_is_hse_incar(user_incar),
            intent="final",
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.RELAX:
        return build_relax_flow(
            structure,
            label=label,
            isif=2 if Modifier.IONS_ONLY in calculation_spec.modifiers else None,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.DOUBLE_RELAX:
        return build_double_relax_flow(
            structure,
            label=label,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.DOS:
        return build_dos_flow(
            structure,
            label=label,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.BAND_STRUCTURE:
        return build_band_structure_flow(
            structure,
            label=label,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
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
    resources=None,
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
        resources=resources,
        potcar_functional=potcar_functional,
    )


def build_atomate2_flow_from_spec(
    structure,
    flow_spec: dict,
    *,
    run_name: str,
    resources=None,
):
    """
    Build the Atomate2 Flow represented by a SubmissionSpec flow_spec.

    Remote execution preserves the notebook behaviour of naming the Jobflow
    Flow after the concrete run directory while reusing the same scientific
    workflow construction used by the browser preview.
    """

    calculation_spec = calculation_spec_from_flow_spec(flow_spec)
    potcar_functional = flow_spec.get("potcar_functional") or "PBE_64"
    execution_resources = resources or flow_spec.get("execution_resources")
    from backend.calculations.builder import build_calculation_flow

    flow = build_calculation_flow(
        structure=structure,
        spec=calculation_spec,
        label=run_name,
        incar=flow_spec.get("incar") or flow_spec.get("incar_overrides") or {},
        kpoints=flow_spec.get("kpoints"),
        resources=execution_resources,
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
    "build_band_structure_flow",
    "build_band_structure_input_set_generator",
    "build_dos_flow",
    "build_dos_input_set_generator",
    "build_double_relax_flow",
    "build_relax_flow",
    "build_relax_input_set_generator",
    "build_static_flow",
    "build_static_input_set_generator",
    "build_vasp_input_set_for_spec",
    "build_vasp_input_set_generator_for_spec",
    "apply_spin_settings",
    "incar_relax",
    "incar_static",
    "ksettings",
    "line_mode_ksettings",
]
