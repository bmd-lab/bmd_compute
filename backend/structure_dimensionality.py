from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pymatgen.core as pymatgen_core
from pymatgen.analysis.dimensionality import (
    get_dimensionality_larsen,
    get_structure_components,
)
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.core import Structure


OBSERVED = "observed"
ANALYSIS_FAILED = "analysis_failed"
METHOD_ID = "pymatgen.crystalnn_larsen_dimensionality"

_LIMITATIONS = (
    "This observation describes bonded periodic connectivity dimensionality.",
    "It does not establish bulk provenance, intrinsic layered bulk character, "
    "the magnitude of van der Waals interactions, or whether dispersion correction is required.",
    "An isolated slab or monolayer in a periodic cell may legitimately have dimensionality 2.",
)


@dataclass(frozen=True)
class StructureDimensionalityComponent:
    dimensionality: int
    formula: str
    orientation: tuple[int, ...] | None = None
    site_ids: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensionality", int(self.dimensionality))
        object.__setattr__(self, "formula", str(self.formula))
        object.__setattr__(
            self,
            "orientation",
            None if self.orientation is None else tuple(int(item) for item in self.orientation),
        )
        object.__setattr__(self, "site_ids", tuple(int(item) for item in self.site_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensionality": self.dimensionality,
            "formula": self.formula,
            "orientation": None if self.orientation is None else list(self.orientation),
            "site_ids": list(self.site_ids),
        }


@dataclass(frozen=True)
class StructureDimensionalityMethod:
    id: str = METHOD_ID
    library: str = "pymatgen"
    pymatgen_version: str | None = None
    bonding: str = "pymatgen.analysis.local_env.CrystalNN.get_bonded_structure"
    dimensionality: str = "pymatgen.analysis.dimensionality.get_dimensionality_larsen"
    components: str = "pymatgen.analysis.dimensionality.get_structure_components"
    neighbor_strategy: str = "CrystalNN defaults"
    component_options: tuple[str, ...] = ("inc_orientation=True", "inc_site_ids=True")

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "library", str(self.library))
        if self.pymatgen_version is None:
            object.__setattr__(self, "pymatgen_version", _pymatgen_version())
        object.__setattr__(self, "bonding", str(self.bonding))
        object.__setattr__(self, "dimensionality", str(self.dimensionality))
        object.__setattr__(self, "components", str(self.components))
        object.__setattr__(self, "neighbor_strategy", str(self.neighbor_strategy))
        object.__setattr__(
            self,
            "component_options",
            tuple(str(item) for item in self.component_options),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "library": self.library,
            "pymatgen_version": self.pymatgen_version,
            "bonding": self.bonding,
            "dimensionality": self.dimensionality,
            "components": self.components,
            "neighbor_strategy": self.neighbor_strategy,
            "component_options": list(self.component_options),
        }


@dataclass(frozen=True)
class StructureDimensionalityObservation:
    status: str
    dimensionality: int | None
    method: StructureDimensionalityMethod = field(default_factory=StructureDimensionalityMethod)
    components: tuple[StructureDimensionalityComponent, ...] = field(default_factory=tuple)
    reason: str | None = None
    limitations: tuple[str, ...] = _LIMITATIONS

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status))
        object.__setattr__(
            self,
            "dimensionality",
            None if self.dimensionality is None else int(self.dimensionality),
        )
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(
            self,
            "reason",
            None if self.reason is None else str(self.reason),
        )
        object.__setattr__(self, "limitations", tuple(str(item) for item in self.limitations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dimensionality": self.dimensionality,
            "method": self.method.to_dict(),
            "components": [component.to_dict() for component in self.components],
            "reason": self.reason,
            "limitations": list(self.limitations),
        }


def observe_structure_dimensionality(structure: Structure) -> StructureDimensionalityObservation:
    """Observe bonded periodic connectivity dimensionality for an in-memory Structure."""

    method = StructureDimensionalityMethod()
    try:
        if not isinstance(structure, Structure):
            raise TypeError("structure must be a pymatgen.core.Structure")
        working_structure = structure.copy()
        bonded = CrystalNN().get_bonded_structure(working_structure)
        dimensionality = get_dimensionality_larsen(bonded)
        components = tuple(
            sorted(
                (
                    _component_from_pymatgen(component)
                    for component in get_structure_components(
                        bonded,
                        inc_orientation=True,
                        inc_site_ids=True,
                    )
                ),
                key=lambda component: (
                    component.site_ids,
                    component.dimensionality,
                    component.formula,
                    component.orientation or (),
                ),
            )
        )
    except Exception as exc:
        return StructureDimensionalityObservation(
            status=ANALYSIS_FAILED,
            dimensionality=None,
            method=method,
            reason=f"{type(exc).__name__}: {exc}",
        )

    return StructureDimensionalityObservation(
        status=OBSERVED,
        dimensionality=dimensionality,
        method=method,
        components=components,
    )


def _component_from_pymatgen(component: dict[str, Any]) -> StructureDimensionalityComponent:
    structure_graph = component.get("structure_graph")
    formula = (
        structure_graph.structure.composition.reduced_formula
        if structure_graph is not None
        else "unknown"
    )
    return StructureDimensionalityComponent(
        dimensionality=component["dimensionality"],
        formula=formula,
        orientation=component.get("orientation"),
        site_ids=component.get("site_ids", ()),
    )


def _pymatgen_version() -> str | None:
    return getattr(pymatgen_core, "__version__", None)


__all__ = [
    "ANALYSIS_FAILED",
    "METHOD_ID",
    "OBSERVED",
    "StructureDimensionalityComponent",
    "StructureDimensionalityMethod",
    "StructureDimensionalityObservation",
    "observe_structure_dimensionality",
]
