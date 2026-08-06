# Calculation Architecture

This document describes how BMD Compute represents VASP calculations as scientific intent.

The goal is to separate what the user wants to calculate from how VASP, pymatgen, atomate2 and Jobflow implement it. The current implementation is intentionally a compatibility layer: it introduces the new architecture without changing existing workflow behaviour.

---

# Core Concepts

## Purpose

`Purpose` describes the scientific task.

Examples:

* `relax`
* `static`
* `dos`
* `band_structure`
* `dielectric`

Purpose should not encode exchange-correlation functionals, INCAR tags, POTCAR names or VASP execution details.

Current compatibility support:

* `static`
* `relax`
* `double_relax`
* `dos`
* `relax` with the compatibility modifier `ions_only`

## Theory

`Theory` describes the exchange-correlation or electronic-structure method.

Examples:

* `pbe`
* `r2scan`
* `hse06`

Theory should map to pymatgen and atomate2 input-set support wherever possible. BMD Compute should not reimplement pymatgen input-set defaults.

Current compatibility support:

* `pbe`, using the existing `PBE_64` POTCAR functional.

## Modifiers

`Modifier` describes optional scientific features layered onto a purpose and theory.

Examples:

* `soc`
* `dft_u`
* `spin_polarized`
* `gamma_only`
* `ions_only`

Modifiers should remain scientific-intent labels. They should not become free-form INCAR fragments. When a modifier requires VASP settings, the builder should prefer existing pymatgen or atomate2 support before applying Burton Lab overrides.

`ions_only` currently exists as a compatibility modifier for the existing `relax_ions` workflow.

---

# CalculationSpec

`CalculationSpec` is the canonical backend object for calculation intent.

```python
CalculationSpec(
    purpose=Purpose.STATIC,
    theory=Theory.PBE,
    modifiers=frozenset(),
    label=None,
)
```

It is defined in:

```text
backend/calculations/models.py
```

Responsibilities:

* normalize user-facing strings into enums
* carry scientific intent through backend layers
* avoid storing VASP implementation details
* provide a stable object that future builders can map to atomate2 makers

`CalculationSpec` should not contain:

* INCAR templates
* KPOINTS templates
* atomate2 maker classes
* pymatgen input-set classes
* SLURM resources
* submission or monitoring state

---

# Builder

`build_calculation_flow()` is the public construction entry point for calculation intent.

It is defined in:

```text
backend/calculations/builder.py
```

Current behaviour:

```text
CalculationSpec
    ↓
validate_calculation_spec()
    ↓
legacy workflow mapping
    ↓
backend.workflows.build_atomate2_flow()
```

This preserves existing behaviour and keeps using the same atomate2 makers as before.

Future behaviour:

```text
CalculationSpec
    ↓
validated Purpose / Theory / Modifiers
    ↓
atomate2 maker selection
    ↓
pymatgen VaspInputSet selection
    ↓
small Burton Lab override policy
    ↓
Jobflow Flow
```

The builder owns orchestration. It should not become a repository of copied pymatgen defaults or VASP input templates.

---

# Registry

The registry validates supported combinations and translates between new and legacy representations during the compatibility phase.

It is defined in:

```text
backend/calculations/registry.py
```

Current responsibilities:

* validate that a `CalculationSpec` is supported
* map legacy workflow strings to `CalculationSpec`
* map `CalculationSpec` back to legacy workflow names
* map supported theories to legacy POTCAR functionals

Current compatibility mapping:

```text
(static,       pbe, no modifiers)        -> static
(relax,        pbe, no modifiers)        -> relax
(double_relax, pbe, no modifiers)        -> double_relax
(dos,          pbe, no modifiers)        -> dos
(relax,        pbe, ions_only modifier)  -> relax_ions
```

Future responsibilities:

* define supported purpose/theory/modifier combinations
* reject unsupported combinations before atomate2 construction
* route supported combinations to the correct builder strategy
* provide metadata to the browser without embedding scientific logic in templates

The registry should answer "is this calculation supported?" It should not generate INCAR settings directly.

---

# Data Files

## presets.yaml

Located at:

```text
backend/calculations/presets.yaml
```

This file contains display and UI metadata:

* display names
* enabled or disabled status
* short descriptions
* default legacy POTCAR functional for compatibility

It should remain declarative metadata. It should not instantiate Python classes or duplicate pymatgen defaults.

## overrides.yaml

Located at:

```text
backend/calculations/overrides.yaml
```

This file is reserved for Burton Lab override policy only.

Allowed content:

* small INCAR overrides required by Burton Lab policy
* small KPOINTS policy overrides
* documented lab-specific deviations from pymatgen or atomate2 defaults

Disallowed content:

* full INCAR templates
* copied pymatgen input-set definitions
* copied atomate2 maker defaults
* workflow logic
* SLURM or deployment settings

---

# Relationship To pymatgen And atomate2

BMD Compute should use pymatgen and atomate2 as the authoritative implementation of VASP scientific defaults.

```text
Purpose / Theory / Modifiers
    describe intent

atomate2 makers
    construct Jobflow jobs and flows

pymatgen VaspInputSets
    generate VASP input files

Burton Lab overrides
    apply small local policy only where necessary
```

The design principle is:

```text
Prefer inheritance from pymatgen and atomate2 over local duplication.
```

When pymatgen adds or improves an input set, BMD Compute should be able to update the mapping layer and inherit the improvement. Local override code should shrink over time, not grow into a parallel input-set system.

---

# UML-Style Data Flow

```text
+-------------------+
| Browser Form      |
| workflow + method |
+---------+---------+
          |
          v
+-------------------+
| FastAPI Controller|
| parse request     |
+---------+---------+
          |
          v
+-------------------+
| CalculationSpec   |
| Purpose           |
| Theory            |
| Modifiers         |
+---------+---------+
          |
          v
+-------------------+
| Registry          |
| validate support  |
| legacy mapping    |
+---------+---------+
          |
          v
+-------------------+
| Builder           |
| compatibility     |
| delegation        |
+---------+---------+
          |
          v
+-------------------+
| backend.workflows |
| existing makers   |
+---------+---------+
          |
          v
+-------------------+
| atomate2 Makers   |
| Jobflow Flow      |
+---------+---------+
          |
          v
+-------------------+
| pymatgen          |
| VaspInputSets     |
+-------------------+
```

Current compatibility path:

```text
CalculationSpec -> Registry -> Builder -> backend.workflows -> atomate2
```

Target path:

```text
CalculationSpec -> Registry -> Builder -> atomate2 makers -> pymatgen input sets
```

---

# Migration Plan

Future PRs should complete the migration in small steps:

1. Use `presets.yaml` to drive browser labels and enabled options.
2. Add explicit registry entries for band structure and dielectric calculations.
3. Map `Theory` values directly to atomate2 and pymatgen-supported input-set options.
4. Add modifier handling only where atomate2 or pymatgen does not already provide a complete abstraction.
5. Move `SubmissionSpec.flow_spec` toward `CalculationSpec` while preserving backwards compatibility for existing submitted jobs.
6. Update remote execution packaging so `backend/calculations/` is available on the cluster.
7. Retire legacy workflow strings once browser, submission and remote execution all use `CalculationSpec`.

Throughout the migration, every PR should preserve existing validated notebook behaviour unless it explicitly changes a scientific policy.
