from __future__ import annotations

from backend.calculations.models import StageType


AUTOMATIC_NCORE_STAGE_TYPES = frozenset(
    {
        StageType.RELAX,
        StageType.STATIC,
        StageType.DOS,
    }
)


def stage_allows_automatic_ncore(stage_type: StageType | str) -> bool:
    return StageType.from_value(stage_type) in AUTOMATIC_NCORE_STAGE_TYPES
