from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class _IntentEnum(str, Enum):
    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value

        normalized = (
            str(value or "")
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
            .replace("+", "_")
            .replace("polarised", "polarized")
        )
        for item in cls:
            if item.value == normalized or item.name.lower() == normalized:
                return item

        raise ValueError(f"Unsupported {cls.__name__}: {value!r}")


class Purpose(_IntentEnum):
    RELAX = "relax"
    STATIC = "static"
    DOS = "dos"
    BAND_STRUCTURE = "band_structure"
    DIELECTRIC = "dielectric"


class Theory(_IntentEnum):
    PBE = "pbe"
    R2SCAN = "r2scan"
    HSE06 = "hse06"


class Modifier(_IntentEnum):
    SOC = "soc"
    DFT_U = "dft_u"
    SPIN_POLARIZED = "spin_polarized"
    GAMMA_ONLY = "gamma_only"
    IONS_ONLY = "ions_only"


def _normalize_modifiers(modifiers: Iterable[Modifier | str] | None) -> frozenset[Modifier]:
    return frozenset(Modifier.from_value(modifier) for modifier in (modifiers or ()))


@dataclass(frozen=True)
class CalculationSpec:
    """
    Scientific intent for a VASP calculation.

    This object intentionally does not describe INCAR tags, input set classes,
    or atomate2 maker implementation details. During the compatibility phase it
    is translated back to the existing workflow names by the registry layer.
    """

    purpose: Purpose | str
    theory: Theory | str = Theory.PBE
    modifiers: frozenset[Modifier] | Iterable[Modifier | str] = field(default_factory=frozenset)
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "purpose", Purpose.from_value(self.purpose))
        object.__setattr__(self, "theory", Theory.from_value(self.theory))
        object.__setattr__(self, "modifiers", _normalize_modifiers(self.modifiers))

    def to_dict(self) -> dict:
        return {
            "purpose": self.purpose.value,
            "theory": self.theory.value,
            "modifiers": sorted(modifier.value for modifier in self.modifiers),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "CalculationSpec":
        values = dict(data or {})
        return cls(
            purpose=values.get("purpose", Purpose.STATIC),
            theory=values.get("theory", Theory.PBE),
            modifiers=values.get("modifiers") or (),
            label=values.get("label"),
        )
