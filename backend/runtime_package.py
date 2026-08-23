from __future__ import annotations

import hashlib
from pathlib import Path


RUNTIME_PACKAGE_DIR = "backend"
RUNTIME_PACKAGE_SOURCE_SUFFIXES = (".py",)
RUNTIME_PACKAGE_EXCLUDED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "tests",
    }
)


def runtime_package_root() -> Path:
    return Path(__file__).resolve().parent


def runtime_package_files() -> tuple[Path, ...]:
    root = runtime_package_root()
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in RUNTIME_PACKAGE_SOURCE_SUFFIXES:
            continue
        if any(part in RUNTIME_PACKAGE_EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)

    return tuple(sorted(files, key=lambda item: item.relative_to(root).as_posix()))


def runtime_package_relative_paths() -> tuple[str, ...]:
    root = runtime_package_root()
    return tuple(path.relative_to(root).as_posix() for path in runtime_package_files())


def build_runtime_package_sources() -> dict[str, str]:
    root = runtime_package_root()
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in runtime_package_files()
    }


def build_runtime_package_manifest() -> dict[str, str]:
    root = runtime_package_root()
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in runtime_package_files()
    }


def runtime_package_manifest_metadata() -> dict:
    manifest = build_runtime_package_manifest()
    return {
        "status": "available",
        "scope": "BMD Compute packaged Python runtime source",
        "package_dir": RUNTIME_PACKAGE_DIR,
        "hash_algorithm": "sha256",
        "files_count": len(manifest),
        "manifest": manifest,
    }


__all__ = [
    "RUNTIME_PACKAGE_DIR",
    "RUNTIME_PACKAGE_EXCLUDED_DIRS",
    "RUNTIME_PACKAGE_SOURCE_SUFFIXES",
    "build_runtime_package_manifest",
    "build_runtime_package_sources",
    "runtime_package_files",
    "runtime_package_manifest_metadata",
    "runtime_package_relative_paths",
    "runtime_package_root",
]
