from __future__ import annotations

from dataclasses import dataclass

from backend.calculations.models import StageType
from backend.calculations.registry import CalculationValidationError
from backend.config import DEFAULT_ACCOUNT, DEFAULT_PARTITION, DEFAULT_RESOURCES


ALLOWED_CPU_COUNTS = (24, 48, 72, 96, 120, 144, 168, 192)
DEFAULT_NCORE = 8
AUTOMATIC_NCORE_STAGE_TYPES = frozenset(
    {
        StageType.RELAX,
        StageType.STATIC,
        StageType.DOS,
    }
)


@dataclass(frozen=True)
class ExecutionResources:
    nodes: int = DEFAULT_RESOURCES["nodes"]
    cpus: int = DEFAULT_RESOURCES["ntasks"]
    memory_gb: int = DEFAULT_RESOURCES["mem_gb"]
    walltime: str = DEFAULT_RESOURCES["walltime"]
    queue: str = DEFAULT_PARTITION
    account: str = DEFAULT_ACCOUNT

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", _positive_int(self.nodes, "nodes"))
        object.__setattr__(self, "cpus", _allowed_cpu_count(self.cpus))
        object.__setattr__(self, "memory_gb", _positive_int(self.memory_gb, "memory"))
        object.__setattr__(self, "walltime", _nonempty_text(self.walltime, "walltime"))
        object.__setattr__(self, "queue", _nonempty_text(self.queue, "queue"))
        object.__setattr__(self, "account", _nonempty_text(self.account, "account"))

    @property
    def ntasks(self) -> int:
        return self.cpus

    @property
    def mem_gb(self) -> int:
        return self.memory_gb

    @property
    def partition(self) -> str:
        return self.queue

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "ntasks": self.ntasks,
            "mem_gb": self.mem_gb,
            "walltime": self.walltime,
            "partition": self.partition,
            "account": self.account,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "ExecutionResources":
        values = dict(data or {})
        return cls(
            nodes=values.get("nodes", DEFAULT_RESOURCES["nodes"]),
            cpus=values.get("cpus", values.get("ntasks", DEFAULT_RESOURCES["ntasks"])),
            memory_gb=values.get(
                "memory_gb",
                values.get("mem_gb", DEFAULT_RESOURCES["mem_gb"]),
            ),
            walltime=values.get("walltime", DEFAULT_RESOURCES["walltime"]),
            queue=values.get("queue", values.get("partition", DEFAULT_PARTITION)),
            account=values.get("account", DEFAULT_ACCOUNT),
        )


def default_execution_resources() -> ExecutionResources:
    return ExecutionResources()


def normalize_execution_resources(resources=None) -> ExecutionResources:
    if isinstance(resources, ExecutionResources):
        return resources

    if isinstance(resources, dict):
        return ExecutionResources.from_dict(resources)

    if resources is None:
        return default_execution_resources()

    raise CalculationValidationError(
        "Execution resources could not be interpreted.",
        suggestion="Update the calculation using valid CPU, memory, walltime, and queue values.",
    )


def ncore_for_execution_resources(resources=None) -> int:
    normalize_execution_resources(resources)
    return DEFAULT_NCORE


def stage_allows_automatic_ncore(stage_type: StageType | str) -> bool:
    return StageType.from_value(stage_type) in AUTOMATIC_NCORE_STAGE_TYPES


def _positive_int(value, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise CalculationValidationError(
            f"{label.capitalize()} must be a positive integer.",
            suggestion="Enter a whole-number resource value and update the calculation again.",
        ) from exc

    if number < 1:
        raise CalculationValidationError(
            f"{label.capitalize()} must be a positive integer.",
            suggestion="Enter a whole-number resource value greater than zero.",
        )

    return number


def _allowed_cpu_count(value) -> int:
    cpus = _positive_int(value, "CPUs")
    if cpus not in ALLOWED_CPU_COUNTS:
        allowed = ", ".join(str(count) for count in ALLOWED_CPU_COUNTS)
        raise CalculationValidationError(
            f"CPUs must be one of: {allowed}.",
            suggestion="Choose one of the listed CPU counts and update the calculation again.",
        )

    return cpus


def _nonempty_text(value, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CalculationValidationError(
            f"{label.capitalize()} must not be empty.",
            suggestion="Enter the execution setting and update the calculation again.",
        )

    return text


__all__ = [
    "ALLOWED_CPU_COUNTS",
    "AUTOMATIC_NCORE_STAGE_TYPES",
    "DEFAULT_NCORE",
    "ExecutionResources",
    "default_execution_resources",
    "ncore_for_execution_resources",
    "normalize_execution_resources",
    "stage_allows_automatic_ncore",
]
