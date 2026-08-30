from __future__ import annotations

from typing import Any, Mapping


DISPERSION_OPTION_KEY = "dispersion"
DISPERSION_METHOD_KEY = "method"
VAN_DER_WAALS_VDW_METHOD = "dftd3-bj"
VAN_DER_WAALS_INCAR_EFFECT = {"IVDW": 12}
DEFAULT_DISPERSION_METHOD = VAN_DER_WAALS_VDW_METHOD

_LEGACY_DISPERSION_METHODS = (
    {
        "value": "dftd3",
        "label": "DFT-D3",
        "incar_effect": {"IVDW": 11},
    },
    {
        "value": "dftd3-bj",
        "label": "DFT-D3(BJ)",
        "incar_effect": {"IVDW": 12},
    },
)

_METHOD_ALIASES = {
    "d3": "dftd3",
    "dft-d3": "dftd3",
    "dftd3": "dftd3",
    "d3-bj": "dftd3-bj",
    "d3bj": "dftd3-bj",
    "dft-d3-bj": "dftd3-bj",
    "dftd3-bj": "dftd3-bj",
    "dftd3bj": "dftd3-bj",
}


def legacy_dispersion_method_options(*, include_incar_effect: bool = False) -> tuple[dict[str, Any], ...]:
    options = []
    for method in _LEGACY_DISPERSION_METHODS:
        payload = {
            "value": method["value"],
            "label": method["label"],
        }
        if include_incar_effect:
            payload["incar_effect"] = dict(method["incar_effect"])
        options.append(payload)
    return tuple(options)


def dispersion_method_options(*, include_incar_effect: bool = False) -> tuple[dict[str, Any], ...]:
    return legacy_dispersion_method_options(include_incar_effect=include_incar_effect)


def normalize_dispersion_method(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return DEFAULT_DISPERSION_METHOD

    normalized = str(value).strip().lower().replace("_", "-").replace(" ", "")
    try:
        return _METHOD_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(method["label"] for method in _LEGACY_DISPERSION_METHODS)
        raise ValueError(f"Unsupported legacy dispersion correction {value!r}. Choose {supported}.") from exc


def dispersion_method_from_options(options: Mapping[str, Any] | None) -> str:
    values = dict(options or {})
    dispersion = values.get(DISPERSION_OPTION_KEY)
    if isinstance(dispersion, Mapping):
        return normalize_dispersion_method(dispersion.get(DISPERSION_METHOD_KEY))
    if isinstance(dispersion, str):
        return normalize_dispersion_method(dispersion)
    return DEFAULT_DISPERSION_METHOD


def dispersion_option_payload(method: str | None = None) -> dict[str, dict[str, str]]:
    return {
        DISPERSION_OPTION_KEY: {
            DISPERSION_METHOD_KEY: normalize_dispersion_method(method),
        }
    }


def van_der_waals_modifier_policy() -> dict[str, Any]:
    return {
        "modifier": "van_der_waals",
        "label": "van der Waals correction",
        "description": "Adds the DFT-D3 dispersion correction with Becke-Johnson damping.",
        "incar_effect": dict(VAN_DER_WAALS_INCAR_EFFECT),
        "upstream_interface": {
            "atomate2_pymatgen_generator_keyword": "vdw",
            "keyword_value": VAN_DER_WAALS_VDW_METHOD,
        },
        "phase_1_support": {
            "theories": ["pbe"],
            "stage_types": ["relax", "static"],
            "blocked_with_modifiers": ["soc"],
            "blocked_terminal_stage_types": ["dos", "band_structure"],
        },
        "legacy_serialized_modifier": {
            "modifier": "dispersion",
            "option_key": DISPERSION_OPTION_KEY,
            "method_key": DISPERSION_METHOD_KEY,
            "methods": list(legacy_dispersion_method_options(include_incar_effect=True)),
            "new_workflows_emit": False,
        },
    }


def dispersion_modifier_policy() -> dict[str, Any]:
    return van_der_waals_modifier_policy()


__all__ = [
    "DEFAULT_DISPERSION_METHOD",
    "DISPERSION_METHOD_KEY",
    "DISPERSION_OPTION_KEY",
    "VAN_DER_WAALS_INCAR_EFFECT",
    "VAN_DER_WAALS_VDW_METHOD",
    "dispersion_method_from_options",
    "dispersion_method_options",
    "dispersion_modifier_policy",
    "dispersion_option_payload",
    "legacy_dispersion_method_options",
    "normalize_dispersion_method",
    "van_der_waals_modifier_policy",
]
