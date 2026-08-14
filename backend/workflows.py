import os
import shlex

ENCUT_STATIC_PREP_DEFAULT = 520
# BMD workflow policy from the validated reference notebook:
# relax stages use 580 eV and final static-style stages use at least 620 eV.
ENCUT_RELAX_DEFAULT = 580
ENCUT_STATIC_FINAL_DEFAULT = 620
BAND_STRUCTURE_LINE_DENSITY_DEFAULT = 40
HSE_BAND_STRUCTURE_RECIPROCAL_DENSITY_DEFAULT = 64
DFT_U_INCAR_KEYS = (
    "LDAU",
    "LDAUTYPE",
    "LDAUL",
    "LDAUU",
    "LDAUJ",
    "LDAUPRINT",
    "LMAXMIX",
)
VASP_STANDARD_EXECUTABLE = "vasp_std"
VASP_NCL_EXECUTABLE = "vasp_ncl"
_KNOWN_VASP_EXECUTABLES = frozenset(
    {
        VASP_STANDARD_EXECUTABLE,
        "vasp_gam",
        VASP_NCL_EXECUTABLE,
    }
)

from backend.config import DEFAULT_VASP_CMD
from backend.calculations.models import (
    CalculationSpec,
    Modifier,
    Purpose,
    StageSpec,
    StageType,
    Theory,
    WorkflowSpec,
)
from backend.calculations.resources import (
    ncore_for_execution_resources,
    stage_allows_automatic_ncore,
)
from backend.calculations.custodian_policy import hse_band_structure_run_vasp_kwargs
from backend.calculations.registry import (
    CalculationValidationError,
    calculation_stage_directories,
    calculation_spec_from_workflow_spec,
    calculation_spec_from_flow_spec,
    calculation_spec_from_legacy,
    legacy_workflow_from_spec,
    validate_calculation_spec,
    validate_workflow_spec,
    workflow_stage_directories,
    workflow_spec_from_flow_spec,
)
from backend.calculations.theory_policy import (
    CalculationStage,
    apply_theory_incar_settings,
    theory_uses_hybrid_functional,
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


def vasp_executable_for_modifiers(modifiers) -> str:
    normalized_modifiers = calculation_modifiers_from_options(modifiers=modifiers)
    if Modifier.SOC in normalized_modifiers:
        return VASP_NCL_EXECUTABLE
    return VASP_STANDARD_EXECUTABLE


def _replace_vasp_executable(command: str | None, executable: str) -> str:
    parts = shlex.split(command or DEFAULT_VASP_CMD)
    if not parts:
        return executable

    for index in range(len(parts) - 1, -1, -1):
        token = parts[index]
        basename = token.replace("\\", "/").rsplit("/", 1)[-1]
        if basename in _KNOWN_VASP_EXECUTABLES:
            parts[index] = token[: len(token) - len(basename)] + executable
            return shlex.join(parts)

    parts.append(executable)
    return shlex.join(parts)


def vasp_command_for_modifiers(modifiers, *, base_command: str | None = None) -> str:
    command = base_command or os.environ.get("VASP_CMD") or DEFAULT_VASP_CMD
    return _replace_vasp_executable(
        command,
        vasp_executable_for_modifiers(modifiers),
    )


def run_vasp_kwargs_for_modifiers(modifiers) -> dict:
    if vasp_executable_for_modifiers(modifiers) == VASP_NCL_EXECUTABLE:
        return {"vasp_cmd": vasp_command_for_modifiers(modifiers)}
    return {}


def apply_modifier_incar_settings(user_incar, *, modifiers) -> dict:
    settings = dict(user_incar or {})
    normalized_modifiers = calculation_modifiers_from_options(modifiers=modifiers)
    spin_polarized = Modifier.SPIN_POLARIZED in normalized_modifiers

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
        settings["GGA_COMPAT"] = False
        settings["LELF"] = None
        settings["ISPIN"] = None

    return settings


def apply_dft_u_settings(user_incar, *, dft_u: bool) -> dict:
    settings = dict(user_incar or {})
    if dft_u:
        return settings

    # Deliberate BMD policy: plain PBE means no Hubbard U unless DFT+U is
    # explicitly selected, even when pymatgen would add chemistry-based U tags.
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


def apply_stage_resource_incar_settings(
    user_incar,
    *,
    stage_type: StageType | str,
    resources=None,
    allow_ncore=True,
) -> dict:
    settings = dict(user_incar or {})
    if allow_ncore and stage_allows_automatic_ncore(stage_type):
        settings["NCORE"] = ncore_for_execution_resources(resources)
    return settings


def ksettings_for_modifiers(structure, kpoints_config, *, modifiers):
    normalized_modifiers = calculation_modifiers_from_options(modifiers=modifiers)
    if Modifier.GAMMA_ONLY in normalized_modifiers:
        return ksettings(structure, {"mode": "gamma", "value": 1})

    return ksettings(structure, kpoints_config)


def line_mode_density(kpoints_config=None):
    if kpoints_config:
        mode = str(kpoints_config.get("mode") or "").strip().lower()
        value = kpoints_config.get("value")
        if mode in ("line", "line_density"):
            return int(float(value))

        if "line_density" in kpoints_config:
            return int(float(kpoints_config["line_density"]))

    return BAND_STRUCTURE_LINE_DENSITY_DEFAULT


def line_mode_ksettings(kpoints_config=None):
    return {"line_density": line_mode_density(kpoints_config)}


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


def _is_vector(value) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False

    try:
        for item in value:
            float(item)
    except (TypeError, ValueError):
        return False

    return True


def _soc_axis_from_settings(settings: dict) -> tuple[float, float, float]:
    value = settings.get("SAXIS") or [0, 0, 1]
    try:
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 1.0)

    norm = (x * x + y * y + z * z) ** 0.5
    if norm <= 0:
        return (0.0, 0.0, 1.0)

    return (x / norm, y / norm, z / norm)


def _soc_vector_from_scalar(value, axis: tuple[float, float, float]) -> list[float]:
    moment = float(value)
    return [component * moment for component in axis]


def _native_magmom_defaults() -> dict:
    from pymatgen.io.vasp.sets import MPRelaxSet

    return dict((MPRelaxSet.CONFIG.get("INCAR") or {}).get("MAGMOM") or {})


def _site_magmom_candidates(site) -> tuple[str, ...]:
    candidates = []
    for attribute in ("species_string", "specie"):
        value = getattr(site, attribute, None)
        if value is not None:
            candidates.append(str(value))

    specie = getattr(site, "specie", None)
    symbol = getattr(specie, "symbol", None)
    if symbol:
        candidates.append(str(symbol))

    element = getattr(specie, "element", None)
    element_symbol = getattr(element, "symbol", None)
    if element_symbol:
        candidates.append(str(element_symbol))

    return tuple(dict.fromkeys(candidates))


def _site_scalar_magmom(site, defaults: dict) -> float:
    properties = getattr(site, "properties", {}) or {}
    if "magmom" in properties:
        value = properties["magmom"]
        if _is_vector(value):
            return float(value[2])
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    specie = getattr(site, "specie", None)
    spin = getattr(specie, "spin", None)
    if spin is not None:
        try:
            return float(spin)
        except (TypeError, ValueError):
            pass

    for candidate in _site_magmom_candidates(site):
        if candidate in defaults:
            return float(defaults[candidate])

    return 0.6


def _magmom_value_for_site(magmom: dict, site):
    for candidate in _site_magmom_candidates(site):
        if candidate in magmom:
            return magmom[candidate]
    return None


def _coerce_soc_magmom_value(value, axis: tuple[float, float, float]):
    if _is_vector(value):
        return [float(component) for component in value]
    return _soc_vector_from_scalar(value, axis)


def _soc_magmom_key_for_site(site) -> str:
    for candidate in _site_magmom_candidates(site):
        if candidate:
            return candidate
    return str(site)


def _magmom_dict_from_site_values(structure, values, axis: tuple[float, float, float]):
    vectors = {}
    for site, value in zip(structure, values):
        key = _soc_magmom_key_for_site(site)
        vectors.setdefault(key, _coerce_soc_magmom_value(value, axis))
    return vectors


def _coerce_soc_magmom_setting(magmom, structure, axis: tuple[float, float, float]):
    defaults = _native_magmom_defaults()

    if isinstance(magmom, dict):
        vectors = {}
        for site in structure:
            key = _soc_magmom_key_for_site(site)
            if key in vectors:
                continue
            value = _magmom_value_for_site(magmom, site)
            if value is None:
                value = _site_scalar_magmom(site, defaults)
            vectors[key] = _coerce_soc_magmom_value(value, axis)
        return vectors

    if isinstance(magmom, (list, tuple)) and magmom:
        if len(magmom) == len(structure):
            return _magmom_dict_from_site_values(structure, magmom, axis)
        if len(magmom) == 3 * len(structure):
            values = [
                [float(magmom[index]), float(magmom[index + 1]), float(magmom[index + 2])]
                for index in range(0, len(magmom), 3)
            ]
            return _magmom_dict_from_site_values(structure, values, axis)

    vectors = {}
    for site in structure:
        key = _soc_magmom_key_for_site(site)
        if key not in vectors:
            vectors[key] = _soc_vector_from_scalar(
                _site_scalar_magmom(site, defaults),
                axis,
            )
    return vectors


def apply_soc_magmom_settings(user_incar, *, structure) -> dict:
    settings = dict(user_incar or {})
    try:
        site_count = len(structure)
    except TypeError:
        return settings

    if site_count <= 0:
        return settings

    try:
        sites = list(structure)
    except TypeError:
        return settings

    if not sites or not all(hasattr(site, "species_string") for site in sites):
        return settings

    axis = _soc_axis_from_settings(settings)
    settings["MAGMOM"] = _coerce_soc_magmom_setting(
        settings.get("MAGMOM"),
        sites,
        axis,
    )
    return settings


def build_relax_input_set_generator(
    structure,
    *,
    isif=None,
    theory=Theory.PBE,
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
    if Modifier.SOC in calculation_modifiers:
        user_incar = apply_soc_magmom_settings(user_incar, structure=structure)
    user_incar.setdefault("ENCUT", ENCUT_RELAX_DEFAULT)
    user_incar.setdefault("EDIFF", 1e-6)
    user_incar.setdefault("ADDGRID", True)
    user_incar.setdefault("EDIFFG", -0.01)
    if isif is not None:
        user_incar["ISIF"] = isif
    policy_theory = Theory.from_value(theory)
    user_incar = apply_theory_incar_settings(
        user_incar,
        theory=policy_theory,
        stage=CalculationStage.RELAX,
    )
    user_incar = apply_stage_resource_incar_settings(
        user_incar,
        stage_type=StageType.RELAX,
        resources=resources,
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
    theory=Theory.PBE,
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
        theory=theory,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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


def build_relax_static_flow(
    structure,
    *,
    label="vasp_run",
    theory=Theory.PBE,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import RelaxMaker, StaticMaker
    from jobflow import Flow

    calculation_theory = Theory.from_value(theory)
    stage_directories = calculation_stage_directories(
        CalculationSpec(
            Purpose.RELAX_STATIC,
            theory=calculation_theory,
            modifiers=modifiers or (),
        )
    )
    relax_stage_dir, static_stage_dir = stage_directories

    relax_generator = build_relax_input_set_generator(
        structure,
        theory=calculation_theory,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
    ).make(structure)

    static_generator = build_static_input_set_generator(
        relax_job.output.structure,
        hse=_is_hse_incar(incar or {}),
        intent="final",
        theory=calculation_theory,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
    ).make(
        relax_job.output.structure,
        prev_dir=relax_job.output.dir_name,
    )

    try:
        return Flow(
            [relax_job, static_job],
            name=f"{label}_relax_static",
            metadata={"bmd_stage_directories": stage_directories},
        )
    except TypeError:
        flow = Flow([relax_job, static_job], name=f"{label}_relax_static")
        flow.bmd_stage_directories = stage_directories
        return flow


def _static_user_incar_settings(
    *,
    hse=False,
    prep_for_gw=False,
    intent="final",
    theory=Theory.PBE,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    resource_stage_type=StageType.STATIC,
    structure=None,
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
    if Modifier.SOC in calculation_modifiers and structure is not None:
        user_incar = apply_soc_magmom_settings(user_incar, structure=structure)

    policy_theory = Theory.HSE06 if hse else Theory.from_value(theory)
    user_incar = apply_theory_incar_settings(
        user_incar,
        theory=policy_theory,
        stage=CalculationStage.STATIC,
    )
    hybrid = theory_uses_hybrid_functional(policy_theory) or _is_hse_incar(user_incar)
    if hybrid:
        for key in ("KPAR", "NPAR"):
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

    if prep_for_gw:
        user_incar = apply_resource_incar_settings(
            user_incar,
            resources=resources,
            allow_ncore=False,
        )
    else:
        user_incar = apply_stage_resource_incar_settings(
            user_incar,
            stage_type=resource_stage_type,
            resources=resources,
        )

    return incar_static(user_incar, allow_ncore=not prep_for_gw)


def _static_restart_incar_settings(
    *,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    resource_stage_type=StageType.STATIC,
    structure=None,
):
    settings = _static_user_incar_settings(
        hse=_is_hse_incar(incar or {}),
        intent="final",
        theory=Theory.HSE06 if _is_hse_incar(incar or {}) else Theory.PBE,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        resource_stage_type=resource_stage_type,
        structure=structure,
    )
    settings["ICHARG"] = 11
    return settings


def _hse_band_structure_incar_settings(
    *,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    structure=None,
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
    if Modifier.SOC in calculation_modifiers and structure is not None:
        user_incar = apply_soc_magmom_settings(user_incar, structure=structure)

    for key in ("ENAUG", "LMIXTAU"):
        user_incar.setdefault(key, None)

    user_incar.setdefault("ADDGRID", True)
    user_incar.setdefault("EDIFF", 1e-6)
    user_incar.setdefault("LORBIT", 11)
    user_incar.setdefault("LREAL", False)
    user_incar.setdefault("PREC", "Accurate")
    try:
        encut_now = int(float(user_incar.get("ENCUT", 0)))
    except Exception:
        encut_now = 0
    user_incar["ENCUT"] = max(encut_now, ENCUT_STATIC_FINAL_DEFAULT)

    user_incar = apply_theory_incar_settings(
        user_incar,
        theory=Theory.HSE06,
        stage=CalculationStage.BAND_STRUCTURE,
    )
    user_incar = apply_stage_resource_incar_settings(
        user_incar,
        stage_type=StageType.BAND_STRUCTURE,
        resources=resources,
    )
    return user_incar


def build_static_input_set_generator(
    structure,
    *,
    hse=False,
    prep_for_gw=False,
    intent="final",
    theory=Theory.PBE,
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
        theory=theory,
        modifiers=calculation_modifiers,
        resources=resources,
        incar=incar,
        structure=structure,
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
    theory=Theory.PBE,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import StaticMaker
    from jobflow import Flow

    hybrid_static = (
        hse
        or theory_uses_hybrid_functional(theory)
        or _is_hse_incar(incar or {})
    )
    generator = build_static_input_set_generator(
        structure,
        hse=hse,
        prep_for_gw=prep_for_gw,
        intent=intent,
        theory=theory,
        spin_polarized=spin_polarized,
        modifiers=modifiers,
        resources=resources,
        incar=incar,
        kpoints=kpoints,
        potcar_functional=potcar_functional,
    )
    maker = StaticMaker(
        input_set_generator=generator,
        name=("hse_static" if hybrid_static else "static"),
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
    )

    return Flow(
        [maker.make(structure)],
        name=label + ("_hse_static" if hybrid_static else "_static"),
    )


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
        resource_stage_type=StageType.DOS,
        structure=structure,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
    theory=Theory.PBE,
    spin_polarized=False,
    modifiers=None,
    resources=None,
    incar=None,
    kpoints=None,
    potcar_functional="PBE_64",
):
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

    policy_theory = Theory.from_value(theory)
    if theory_uses_hybrid_functional(policy_theory):
        from atomate2.vasp.sets.core import HSEBSSetGenerator

        band_incar = _hse_band_structure_incar_settings(
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=incar,
            structure=structure,
        )
        return HSEBSSetGenerator(
            mode="line",
            line_density=line_mode_density(kpoints),
            reciprocal_density=HSE_BAND_STRUCTURE_RECIPROCAL_DENSITY_DEFAULT,
            user_potcar_functional=potcar_functional,
            user_incar_settings=band_incar,
        )

    from atomate2.vasp.sets.core import NonSCFSetGenerator

    # Keep the band path grid and basis compatible with the preceding static CHGCAR.
    band_incar = _static_restart_incar_settings(
        spin_polarized=spin_polarized,
        modifiers=calculation_modifiers,
        resources=resources,
        incar=incar,
        resource_stage_type=StageType.BAND_STRUCTURE,
        structure=structure,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
    ).make(
        relax_job.output.structure,
        prev_dir=relax_job.output.dir_name,
    )

    band_generator = build_band_structure_input_set_generator(
        static_job.output.structure,
        theory=Theory.PBE,
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
        run_vasp_kwargs=run_vasp_kwargs_for_modifiers(modifiers),
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


def _stage_options(stage: StageSpec) -> dict:
    return dict(stage.options or {})


def _stage_incar(stage: StageSpec, global_incar: dict | None) -> dict:
    user_incar = dict(global_incar or {})
    user_incar.update(_stage_options(stage).get("incar") or {})
    return user_incar


def _stage_kpoints(stage: StageSpec, global_kpoints):
    return _stage_options(stage).get("kpoints", global_kpoints)


def _single_stage_job_name(stage: StageSpec, user_incar: dict | None = None) -> str:
    if stage.stage_type is StageType.RELAX:
        return "relax_ions" if Modifier.IONS_ONLY in stage.modifiers else "relax"

    if stage.stage_type is StageType.STATIC:
        return "hse_static" if theory_uses_hybrid_functional(stage.theory) or _is_hse_incar(user_incar or {}) else "static"

    if stage.stage_type is StageType.DOS:
        return "dos"

    if stage.stage_type is StageType.BAND_STRUCTURE:
        return "band_structure"

    return stage.stage_type.value


def _workflow_flow_suffix(workflow: WorkflowSpec) -> str:
    compatible_spec = calculation_spec_from_workflow_spec(workflow)
    if compatible_spec is not None:
        if (
            compatible_spec.purpose is Purpose.RELAX
            and Modifier.IONS_ONLY not in compatible_spec.modifiers
        ):
            return ""
        if (
            compatible_spec.purpose is Purpose.STATIC
            and theory_uses_hybrid_functional(compatible_spec.theory)
        ):
            return "hse_static"
        return legacy_workflow_from_spec(compatible_spec)

    return "custom_workflow"


def _flow_with_stage_metadata(jobs, *, name: str, stage_directories: tuple[str, ...]):
    from jobflow import Flow

    if stage_directories:
        try:
            return Flow(
                jobs,
                name=name,
                metadata={"bmd_stage_directories": stage_directories},
            )
        except TypeError:
            flow = Flow(jobs, name=name)
            flow.bmd_stage_directories = stage_directories
            return flow

    return Flow(jobs, name=name)


def build_atomate2_flow_for_workflow_spec(
    structure,
    workflow_spec: WorkflowSpec,
    *,
    label="vasp_run",
    incar=None,
    kpoints=None,
    resources=None,
    potcar_functional="PBE_64",
):
    from atomate2.vasp.jobs.core import NonSCFMaker, RelaxMaker, StaticMaker

    workflow = validate_workflow_spec(workflow_spec)
    stage_directories = workflow_stage_directories(workflow)
    jobs = []
    previous_job = None

    for index, stage in enumerate(workflow.stages):
        user_incar = _stage_incar(stage, incar)
        stage_kpoints = _stage_kpoints(stage, kpoints)
        stage_structure = (
            previous_job.output.structure
            if previous_job is not None
            else structure
        )
        stage_name = (
            stage_directories[index]
            if stage_directories
            else _single_stage_job_name(stage, user_incar)
        )
        spin_polarized = Modifier.SPIN_POLARIZED in stage.modifiers
        run_vasp_kwargs = run_vasp_kwargs_for_modifiers(stage.modifiers)

        if stage.stage_type is StageType.RELAX:
            generator = build_relax_input_set_generator(
                stage_structure,
                isif=2 if Modifier.IONS_ONLY in stage.modifiers else None,
                theory=stage.theory,
                spin_polarized=spin_polarized,
                modifiers=stage.modifiers,
                resources=resources,
                incar=user_incar,
                kpoints=stage_kpoints,
                potcar_functional=potcar_functional,
            )
            job = RelaxMaker(
                input_set_generator=generator,
                name=stage_name,
                run_vasp_kwargs=run_vasp_kwargs,
            ).make(stage_structure)

        elif stage.stage_type is StageType.STATIC:
            generator = build_static_input_set_generator(
                stage_structure,
                hse=_is_hse_incar(user_incar),
                intent="final",
                theory=stage.theory,
                spin_polarized=spin_polarized,
                modifiers=stage.modifiers,
                resources=resources,
                incar=user_incar,
                kpoints=stage_kpoints,
                potcar_functional=potcar_functional,
            )
            maker = StaticMaker(
                input_set_generator=generator,
                name=stage_name,
                run_vasp_kwargs=run_vasp_kwargs,
            )
            if previous_job is None:
                job = maker.make(stage_structure)
            else:
                job = maker.make(
                    stage_structure,
                    prev_dir=previous_job.output.dir_name,
                )

        elif stage.stage_type is StageType.DOS:
            generator = build_dos_input_set_generator(
                stage_structure,
                spin_polarized=spin_polarized,
                modifiers=stage.modifiers,
                resources=resources,
                incar=user_incar,
                kpoints=stage_kpoints,
                potcar_functional=potcar_functional,
            )
            job = NonSCFMaker(
                input_set_generator=generator,
                name=stage_name,
                run_vasp_kwargs=run_vasp_kwargs,
            ).make(
                stage_structure,
                prev_dir=previous_job.output.dir_name,
                mode="uniform",
            )

        elif stage.stage_type is StageType.BAND_STRUCTURE:
            generator = build_band_structure_input_set_generator(
                stage_structure,
                theory=stage.theory,
                spin_polarized=spin_polarized,
                modifiers=stage.modifiers,
                resources=resources,
                incar=user_incar,
                kpoints=stage_kpoints,
                potcar_functional=potcar_functional,
            )
            if theory_uses_hybrid_functional(stage.theory):
                from atomate2.vasp.jobs.core import HSEBSMaker

                hse_run_vasp_kwargs = hse_band_structure_run_vasp_kwargs()
                hse_run_vasp_kwargs.update(run_vasp_kwargs)
                job = HSEBSMaker(
                    input_set_generator=generator,
                    name=stage_name,
                    run_vasp_kwargs=hse_run_vasp_kwargs,
                ).make(
                    stage_structure,
                    prev_dir=previous_job.output.dir_name,
                    mode="line",
                )
            else:
                job = NonSCFMaker(
                    input_set_generator=generator,
                    name=stage_name,
                    run_vasp_kwargs=run_vasp_kwargs,
                ).make(
                    stage_structure,
                    prev_dir=previous_job.output.dir_name,
                    mode="line",
                )

        else:
            raise CalculationValidationError(
                f"Unsupported stage type: {stage.stage_type.value}"
            )

        jobs.append(job)
        previous_job = job

    suffix = _workflow_flow_suffix(workflow)
    flow_name = label if not suffix else f"{label}_{suffix}"
    return _flow_with_stage_metadata(
        jobs,
        name=flow_name,
        stage_directories=stage_directories,
    )


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
            theory=calculation_spec.theory,
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
            theory=calculation_spec.theory,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.RELAX_STATIC:
        return build_static_input_set_generator(
            structure,
            hse=_is_hse_incar(user_incar),
            intent="final",
            theory=calculation_spec.theory,
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
            theory=calculation_spec.theory,
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
            theory=calculation_spec.theory,
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
            theory=calculation_spec.theory,
            spin_polarized=spin_polarized,
            modifiers=calculation_modifiers,
            resources=resources,
            incar=user_incar,
            kpoints=kpoints,
            potcar_functional=potcar_functional,
        )

    if calculation_spec.purpose is Purpose.RELAX_STATIC:
        return build_relax_static_flow(
            structure,
            label=label,
            theory=calculation_spec.theory,
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

    workflow_spec = workflow_spec_from_flow_spec(flow_spec)
    potcar_functional = flow_spec.get("potcar_functional") or "PBE_64"
    execution_resources = resources or flow_spec.get("execution_resources")
    from backend.calculations.builder import build_calculation_flow

    flow = build_calculation_flow(
        structure=structure,
        spec=workflow_spec,
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
    "build_atomate2_flow_for_workflow_spec",
    "build_band_structure_flow",
    "build_band_structure_input_set_generator",
    "build_dos_flow",
    "build_dos_input_set_generator",
    "build_double_relax_flow",
    "build_relax_static_flow",
    "build_relax_flow",
    "build_relax_input_set_generator",
    "build_static_flow",
    "build_static_input_set_generator",
    "build_vasp_input_set_for_spec",
    "build_vasp_input_set_generator_for_spec",
    "apply_soc_magmom_settings",
    "apply_stage_resource_incar_settings",
    "apply_spin_settings",
    "incar_relax",
    "incar_static",
    "ksettings",
    "line_mode_density",
    "line_mode_ksettings",
    "run_vasp_kwargs_for_modifiers",
    "vasp_command_for_modifiers",
    "vasp_executable_for_modifiers",
]
