# atomate2 / pymatgen Capability Status

Updated: 2026-08-19

This document records how the currently installed atomate2/pymatgen VASP surface maps to BMD Compute. It is not a promise that every upstream maker is exposed in the UI.

BMD Compute exposes only reviewed scientific workflows. Upstream availability alone is not sufficient for student-facing support.

## Status Categories

| Status | Meaning |
| --- | --- |
| Implemented and validated | Implemented in BMD Compute and validated by tests plus at least one reviewed/live calculation path where noted. |
| Implemented, tests only | Implemented and covered locally, but not independently PowerSLURM-validated as a separate scientific claim. |
| Deliberately unsupported | Upstream support may exist, but BMD Compute blocks it because the scientific or operational policy is not reviewed. |
| Future candidate | Upstream primitives exist and are plausible future work. |
| Unavailable | No clear installed native atomate2/pymatgen path has been identified. |

## Implemented Capabilities

### PBE Geometry Optimisation

Status: implemented and validated through the core BMD workflow path.

Primary primitives:

- `atomate2.vasp.sets.core.RelaxSetGenerator`
- `atomate2.vasp.jobs.core.RelaxMaker`

BMD policy keeps the Burton Lab relax settings, including the 580 eV ENCUT policy and relaxation convergence settings.

### PBE Static Energy

Status: implemented and validated through the core BMD workflow path.

Primary primitives:

- `atomate2.vasp.sets.core.StaticSetGenerator`
- `atomate2.vasp.jobs.core.StaticMaker`

BMD policy applies the final/static settings, including the 620 eV ENCUT policy, charge-density output, ELF for ordinary collinear PBE static calculations, and resource-derived NCORE where eligible.

### Double Geometry Optimisation

Status: implemented.

Primary primitives:

- two ordered PBE relax stages
- preserved historical stage directories `relax_01/` and `relax_02/`

### PBE Relax -> Static

Status: implemented.

Primary primitives:

- ordered relax and static stages
- runtime static stage consumes the relaxed structure from the preceding relax stage

### PBE Density of States

Status: implemented with workflow-specific results.

Primary primitives:

- preceding Static Energy stage
- `NonSCFSetGenerator` / `NonSCFMaker` style non-SCF DOS path
- pymatgen parsing from `vasprun.xml`
- Plotly DOS visualization

The DOS stage inherits grid- and basis-defining settings from the preceding static stage where required for CHGCAR compatibility.

### PBE Band Structure

Status: implemented with workflow-specific results.

Primary primitives:

- preceding Static Energy stage
- line-mode band-structure stage using atomate2/pymatgen high-symmetry path generation
- pymatgen parsing from `vasprun.xml` plus KPOINTS
- Plotly band visualization

The result renderer includes high-symmetry label conversion, Fermi-level alignment, spin-aware legend behavior, a default `[-10, 10]` eV viewport, and PNG export.

### HSE06 Geometry Optimisation

Status: implemented.

Primary primitives:

- HSE relax stage through atomate2/pymatgen HSE relax support
- centralized HSE06 theory/stage policy

BMD policy:

```text
LHFCALC = True
AEXX = 0.25
HFSCREEN = 0.2
GGA = PE
PRECFOCK = Fast
```

### HSE06 Static Energy

Status: implemented and specifically benchmarked for resource policy.

Primary primitives:

- HSE static stage through atomate2/pymatgen HSE static support
- centralized HSE06 theory/stage policy

BMD policy:

```text
LHFCALC = True
AEXX = 0.25
HFSCREEN = 0.2
GGA = PE
PRECFOCK = Accurate
ISMEAR = 0
```

Resource-derived NCORE remains independent of theory. A 24-rank HSE06 static benchmark showed strong benefit from the existing `NCORE = 8` policy.

### HSE06 Relax -> Static

Status: implemented as an ordered stage-first workflow.

The static stage consumes the relaxed structure from the HSE06 relax stage. Theory policy remains stage-specific, so relax uses `PRECFOCK = Fast` and static uses `PRECFOCK = Accurate`.

### HSE06 Band Structure

Status: implemented and reviewed through the stage-first path.

Supported sequence:

```text
Geometry Optimisation - PBE
Static Energy         - HSE06
Band Structure        - HSE06
```

The PBE relax supplies geometry only. The electronic precursor for the HSE06 band calculation must be HSE06 Static Energy.

Primary primitives:

- `HSEBSSetGenerator(mode="line")`
- `HSEBSMaker`

The generated KPOINTS file may contain a combined weighted uniform mesh plus zero-weight high-symmetry line path. Remote preparation must transfer this as file data, not as shell command text.

### Spin Polarised Calculations

Status: implemented for supported PBE/HSE06 stages where the registry allows the modifier.

BMD Compute preserves pymatgen/atomate2 magnetic initialization where appropriate. SOC stages convert initial moments into vector `MAGMOM`.

### Explicit DFT+U

Status: implemented for supported PBE stages when the selected input set provides reviewed active U values.

BMD Compute does not silently inherit DFT+U into ordinary PBE. If DFT+U is requested and no reviewed U values are available, validation fails clearly.

### Spin-Orbit Coupling

Status: implemented narrowly for reviewed PBE Static Energy stages.

Validated examples include:

- PBE Static -> PBE Static + SOC on Si
- PBE + DFT+U Static -> PBE + DFT+U + SOC Static on Fe2O3

SOC policy includes:

```text
LSORBIT = True
LNONCOLLINEAR = True
GGA_COMPAT = False
ISYM = 0
SAXIS = 0 0 1
ISPIN omitted
LELF omitted
vector MAGMOM
vasp_ncl executable
```

## Deliberately Unsupported Or Unvalidated

### HSE06 DOS

Status: deliberately unsupported.

No reviewed BMD Compute HSE06 DOS implementation is enabled. Do not infer support from HSE06 Static or HSE06 Band Structure.

### HSE06 + SOC

Status: deliberately unsupported.

The current SOC policy is reviewed for PBE Static Energy stages, not hybrid-functional non-collinear calculations.

### SOC Relaxation, SOC DOS, SOC Band Structure

Status: deliberately unsupported.

These require separate scientific review before being exposed.

### r2SCAN

Status: future candidate.

Potential upstream primitives include MP2024/r2SCAN-oriented relax/static makers and pymatgen input sets, but BMD Compute has not selected or validated a lab policy.

### Dielectric And Optics

Status: future candidate.

Potential upstream primitives include `DielectricMaker`, `PolarizationMaker`, and `OpticsMaker`. These are not exposed.

### GW, Elastic, EOS, NEB, Phonon, MD, LOBSTER, AMSET

Status: future candidates or unavailable depending on optional dependencies.

These remain outside the introductory workflow surface and should not be added without separate scientific and operational review.

## Optional Dependency Notes

Some atomate2 modules may require optional packages such as phonopy, seekpath, pymatgen-analysis-diffusion, or defect-analysis packages. BMD Compute should not expose workflows that require missing optional dependencies until the production environment is deliberately updated and tested.

## Implementation Guidance

Prefer native atomate2/pymatgen primitives over local VASP templates.

For new workflows:

1. identify the native maker/input-set path
2. validate precursor and restart requirements
3. add centralized theory/stage/resource policy only where needed
4. add generated-preview and remote-reconstruction tests
5. add result rendering only through the workflow-specific visualization framework
6. document whether the capability is implemented, cluster-validated, or deliberately blocked
