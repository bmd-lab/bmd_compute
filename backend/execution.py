from __future__ import annotations

import sys


def print_runtime_info() -> None:
    print("[runner] python:", sys.version.replace("\n", " "))

    for package in ("atomate2", "jobflow", "pymatgen", "custodian"):
        try:
            module = __import__(package)
            version = getattr(module, "__version__", "<no __version__>")
            print(f"[runner] {package} version:", version)
        except Exception as exc:
            print(f"[runner] {package} import failed:", exc)


def run_submission(spec: dict) -> None:
    print_runtime_info()

    from backend.parser import structure_from_spec
    from backend.workflows import build_atomate2_flow_from_spec
    from jobflow.managers.local import run_locally

    flow_spec = spec["flow_spec"]
    structure = structure_from_spec(flow_spec["structure"])
    flow = build_atomate2_flow_from_spec(
        structure,
        flow_spec,
        run_name=spec["run_name"],
    )

    try:
        run_locally(flow, ensure_success=True, create_folders=False)
    except TypeError:
        run_locally(flow, ensure_success=True)
    print("JOBFLOW_LOCAL_DONE")


__all__ = [
    "print_runtime_info",
    "run_submission",
]
