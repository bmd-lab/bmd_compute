# atomate2 / pymatgen VASP Capability Survey

Generated: 2026-07-26

This document surveys the installed atomate2 and pymatgen capabilities relevant
to BMD Compute's future `CalculationSpec` mapping.

The purpose of this document is roadmap planning only. It does not imply that
BMD Compute currently exposes or supports all listed calculations.

## Survey Environment

Primary environment used for the capability inventory:

```text
/Users/leeburton/miniforge3/envs/bmd-compute/bin/python
atomate2 = 0.1.4
pymatgen = 2026.5.4
jobflow = 0.1.19
custodian = 2025.12.14
```

Other scientific environments detected locally:

| Environment | atomate2 | pymatgen | jobflow | custodian | Notes |
|---|---:|---:|---:|---:|---|
| `/Users/leeburton/micromamba/envs/atomate2_env` | 0.0.21 | 2025.6.14 | 0.2.0 | 2025.5.12 | Older atomate2 API |
| `/Users/leeburton/micromamba/envs/atomate2_local` | 0.0.21.post28+geb2cd1de | 2025.6.14 | 0.2.0 | 2025.5.12 | Older local development build |
| `/usr/bin/python3` | not installed | not installed | not installed | not installed | System Python, not suitable for BMD Compute scientific tests |

Unless noted otherwise, statuses below refer to the primary
`bmd-compute` environment.

## Status Legend

| Status | Meaning |
|---|---|
| recommended | Best candidate for a future BMD Compute mapping, or the current implementation |
| available | Installed upstream support exists, but it is not the preferred first mapping |
| partial support | Upstream support exists but needs composition, optional dependencies, validation, or BMD integration |
| unavailable | No direct installed maker/input-set path identified |
| deprecated | Legacy or compatibility path; avoid for new BMD mappings unless needed to reproduce old data |

## Current Burton Lab Overrides

BMD Compute currently applies Burton Lab overrides only through
`backend/workflows.py`.

Current implemented combinations:

| Purpose | Theory | InputSet | Maker | Status | Burton INCAR overrides |
|---|---|---|---|---|---|
| Relax | PBE | `atomate2.vasp.sets.core.RelaxSetGenerator` | `atomate2.vasp.jobs.core.RelaxMaker` | recommended/current | Yes |
| Relax Ions | PBE | `atomate2.vasp.sets.core.RelaxSetGenerator` | `atomate2.vasp.jobs.core.RelaxMaker` with `ISIF=2` | recommended/current | Yes |
| Static | PBE | `atomate2.vasp.sets.core.StaticSetGenerator` | `atomate2.vasp.jobs.core.StaticMaker` | recommended/current | Yes |

Relax overrides currently include:

```text
ENCUT >= 580
ISPIN = 2
EDIFF = 1e-6
ADDGRID = True
EDIFFG = -0.01
LCHARG = False unless explicitly supplied
LWAVE = False unless explicitly supplied
LAECHG/LVTOT/LELF/LVHAR/LORBIT disabled unless explicitly supplied
GGA/ENAUG/LMIXTAU set to None by default
ALGO = Fast
NCORE = 2 for non-HSE
ISIF = 2 for Relax Ions
```

Static overrides currently include:

```text
ENCUT >= 620
LWAVE = False
LCHARG = True
ISMEAR = -5
SIGMA = 0.05
NEDOS = 4001
LORBIT = 11
LREAL = False
PREC = Accurate
ADDGRID = True
EDIFF = 1e-6
ALGO = Normal
LVTOT/LAECHG/LELF enabled
GGA/ENAUG/LMIXTAU set to None by default
NCORE = 2 for non-HSE
```

There is a legacy HSE detection path inside the static builder when HSE INCAR
tags are manually supplied. That path is not a native `Theory.HSE06` mapping
and should be treated as partial compatibility behavior, not a completed
architecture.

## Purpose: Relax

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `RelaxSetGenerator` | `RelaxMaker` | recommended/current | Yes |
| `TightRelaxSetGenerator` | `TightRelaxMaker` | available | No |
| `RelaxConstVolSetGenerator` / `VaspInputGenerator` | `RelaxConstVolMaker` | available | No |
| `TightRelaxConstVolSetGenerator` | `TightRelaxConstVolMaker` | available | No |
| `TightRelaxConstVolSetGenerator` | `TightConstVolRelaxMaker` | available | No |
| `MPRelaxSet` | `MPGGARelaxMaker` | available | No |
| `MP24RelaxSet` | `MP24PreRelaxMaker` | partial support | No |
| `MP24RelaxSet` | `MP24RelaxMaker` | partial support | No |
| `MPRelaxSet` | `BulkRelaxMaker` | available, slab/adsorption specific | No |
| `MPRelaxSet` | `SlabRelaxMaker` | available, slab/adsorption specific | No |
| `MPRelaxSet` | `MolRelaxMaker` | available, molecule specific | No |

Recommendation: preserve current `RelaxSetGenerator` + `RelaxMaker` for
notebook-equivalent PBE relaxation. Consider `MPGGARelaxMaker` only if BMD
Compute later adds a distinct "Materials Project GGA" preset.

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MPScanRelaxSet` | `MPMetaGGARelaxMaker` | available | No |
| `MP24RelaxSet` | `MP24RelaxMaker` | recommended future candidate | No |
| `MP24RelaxSet` | `MP24PreRelaxMaker` | available, pre-relax stage | No |
| `MPScanRelaxSet` | pymatgen input set only | available | No |
| `MVLScanRelaxSet` | pymatgen input set only | available, MVL-specific | No |

Recommendation: evaluate `MP24RelaxMaker` first for new r2SCAN relax workflows
because its docstring identifies it as an MP2024 r2SCAN relaxation maker.
Use `MPMetaGGARelaxMaker` if the desired policy is the older MPScan/r2SCAN
path.

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `HSERelaxSetGenerator` | `HSERelaxMaker` | recommended future candidate | No native mapping |
| `HSETightRelaxSetGenerator` | `HSETightRelaxMaker` | available | No native mapping |
| `MPHSERelaxSet` | pymatgen input set only | available | No native mapping |

Recommendation: implement `Theory.HSE06` relaxation through
`HSERelaxMaker`/`HSERelaxSetGenerator`, not by manually injecting HSE INCAR
tags into the PBE static/relax path.

## Purpose: Static

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `StaticSetGenerator` | `StaticMaker` | recommended/current | Yes |
| `MPStaticSet` | `MPGGAStaticMaker` | available | No |
| `MP24StaticSet` | `MP24StaticMaker` | partial support, MP2024/r2SCAN-oriented | No |
| `MatPESStaticSet` | `MatPesGGAStaticMaker` | available, specialized | No |
| `MPRelaxSet` | `SlabStaticMaker` | available, slab/adsorption specific | No |
| `MPRelaxSet` | `MolStaticMaker` | available, molecule specific | No |
| `StaticSetGenerator` | `TransmuterMaker` | available, structure transformation workflow | No |

Recommendation: keep `StaticSetGenerator` + `StaticMaker` for current PBE
static calculations. `MPGGAStaticMaker` is the likely future candidate for a
separate Materials Project GGA preset.

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MPScanStaticSet` | `MPMetaGGAStaticMaker` | available | No |
| `MP24StaticSet` | `MP24StaticMaker` | recommended future candidate | No |
| `MPScanStaticSet` | pymatgen input set only | available | No |
| `MatPESStaticSet` | `MatPesMetaGGAStaticMaker` | available, specialized | No |

Recommendation: evaluate `MP24StaticMaker` first for a future MP2024 r2SCAN
static calculation. Use `MPMetaGGAStaticMaker` for the older MPScan/r2SCAN
path.

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `HSEStaticSetGenerator` | `HSEStaticMaker` | recommended future candidate | Partial legacy HSE handling only |
| `MPHSEBSSet` | pymatgen input set only | available for HSE band path, not static-only | No native mapping |

Recommendation: implement `Theory.HSE06` static through
`HSEStaticMaker`/`HSEStaticSetGenerator`. Any lab-specific HSE settings should
be small `user_incar_settings` overrides layered on top.

## Purpose: DOS / Non-SCF

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `NonSCFSetGenerator` | `NonSCFMaker` | recommended future candidate | No |
| `MPNonSCFSet` | pymatgen input set only | available | No |
| `BandStructureMaker` child `NonSCFMaker` | `BandStructureMaker` | available, flow-level | No |
| `UniformBandStructureMaker` child `NonSCFMaker` | `UniformBandStructureMaker` | available, uniform mesh | No |

Recommendation: use `NonSCFMaker` with `NonSCFSetGenerator` for a native DOS
purpose after a preceding static calculation. Do not build a local DOS INCAR
template.

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| r2SCAN-specific non-SCF generator | direct maker unavailable | partial support | No |
| `NonSCFSetGenerator` with r2SCAN-compatible upstream settings | `NonSCFMaker` | partial support, requires validation | No |

Recommendation: defer until the r2SCAN static path is implemented and validate
whether atomate2 has a supported r2SCAN non-SCF composition for the installed
version.

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| HSE DOS-specific input set | direct maker unavailable | unavailable | No |
| `HSEBSSetGenerator` | `HSEBSMaker` | partial support for HSE band path, not DOS-specific | No |

Recommendation: do not expose HSE DOS until a validated upstream composition is
identified.

## Purpose: Band Structure

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `StaticSetGenerator` + `NonSCFSetGenerator` | `BandStructureMaker` | recommended future candidate | No |
| `StaticSetGenerator` + `NonSCFSetGenerator` | `LineModeBandStructureMaker` | available | No |
| `StaticSetGenerator` + `NonSCFSetGenerator` | `UniformBandStructureMaker` | available | No |
| relax flow + band structure flow | `RelaxBandStructureMaker` | available | No |

Recommendation: start with `BandStructureMaker` or explicit line/uniform flow
makers once the UI has a Band Structure purpose.

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| r2SCAN static + non-SCF composition | direct maker unavailable | partial support | No |

Recommendation: defer until r2SCAN static and non-SCF support are validated.

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `HSEBSSetGenerator` | `HSEBSMaker` | recommended future candidate | No |
| `HSEStaticSetGenerator` + `HSEBSSetGenerator` | `HSEBandStructureMaker` | recommended future candidate | No |
| `HSEStaticSetGenerator` + `HSEBSSetGenerator` | `HSELineModeBandStructureMaker` | available | No |
| `HSEStaticSetGenerator` + `HSEBSSetGenerator` | `HSEUniformBandStructureMaker` | available | No |
| `MPHSEBSSet` | pymatgen input set only | available | No |

Recommendation: use the atomate2 HSE band-structure flow makers; they encode
the self-consistent HSE band-structure requirements better than a local
template would.

## Purpose: Dielectric / Optics

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `StaticSetGenerator` | `DielectricMaker` | recommended future candidate for dielectric | No |
| `StaticSetGenerator` | `PolarizationMaker` | available | No |
| `StaticSetGenerator` | `OpticsMaker` | available, flow-level | No |
| `MPAbsorptionSet` | pymatgen input set only | available | No |

Recommendation: map a future Dielectric purpose to `DielectricMaker` first.
Treat optical absorption as a separate purpose or modifier.

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| r2SCAN dielectric/optics maker | direct maker unavailable | partial support | No |

Recommendation: defer until r2SCAN static support is implemented and a
validated upstream dielectric composition is identified.

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `HSEStaticSetGenerator` | `HSEOpticsMaker` | available | No |

Recommendation: available upstream, but validate cost and convergence policy
before exposing to students.

## Purpose: Equation of State

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `EosSetGenerator` | `EosRelaxMaker` | available | No |
| `MPGGAEosRelaxSetGenerator` | `MPGGAEosRelaxMaker` | available | No |
| `MPGGAEosStaticSetGenerator` | `MPGGAEosStaticMaker` | available | No |
| `MPLegacyEosRelaxSetGenerator` | `MPLegacyEosRelaxMaker` | deprecated | No |
| `MPLegacyEosStaticSetGenerator` | `MPLegacyEosStaticMaker` | deprecated | No |
| composed EOS flow | `EosMaker` | available | No |
| composed double-relax EOS flow | `EosDoubleRelaxMaker` | available | No |
| composed MP GGA EOS flow | `MPGGAEosMaker` | available | No |
| composed MP GGA double-relax EOS flow | `MPGGAEosDoubleRelaxMaker` | available | No |
| composed MP legacy EOS flow | `MPLegacyEosMaker` | deprecated | No |
| composed MP legacy double-relax EOS flow | `MPLegacyEosDoubleRelaxMaker` | deprecated | No |

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MPMetaGGAEosPreRelaxSetGenerator` | `MPMetaGGAEosPreRelaxMaker` | available | No |
| `MPMetaGGAEosRelaxSetGenerator` | `MPMetaGGAEosRelaxMaker` | available | No |
| `MPMetaGGAEosStaticSetGenerator` | `MPMetaGGAEosStaticMaker` | available | No |
| composed meta-GGA EOS flow | `MPMetaGGAEosMaker` | available | No |
| composed meta-GGA double-relax EOS flow | `MPMetaGGAEosDoubleRelaxMaker` | available | No |

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| HSE EOS maker | direct maker unavailable | unavailable | No |

Recommendation: keep EOS out of the initial student UI until Relax/Static/DOS
and Band Structure are stable.

## Purpose: Elastic

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `StaticSetGenerator` | `ElasticRelaxMaker` | available | No |
| composed elastic flow | `ElasticMaker` | available | No |
| `MVLElasticSet` | pymatgen input set only | available | No |

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE elastic maker | unavailable | unavailable | No |

## Purpose: Molecular Dynamics

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MDSetGenerator` | `MDMaker` | available | No |
| composed MD flow | `MultiMDMaker` | available | No |
| `MITMDSet` | pymatgen input set only | available | No |
| `MPMDSet` | pymatgen input set only | available | No |
| `MVLNPTMDSet` | pymatgen input set only | available | No |

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE MD maker | unavailable | unavailable | No |

## Purpose: NEB

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `NebSetGenerator` | `NebFromImagesMaker` | available | No |
| composed NEB from endpoints | `NebFromEndpointsMaker` | available | No |
| `ApproxNebSetGenerator` | `ApproxNebImageRelaxMaker` | partial support | No |
| approximate NEB host relax | `ApproxNebHostRelaxMaker` | partial support | No |
| `NEBSet` | pymatgen input set only | available | No |
| `MITNEBSet` | pymatgen input set only | available | No |
| `CINEBSet` | pymatgen input set only | available | No |

Optional dependency issue: `atomate2.vasp.flows.approx_neb` did not import in
the surveyed environment because `pymatgen-analysis-diffusion` is not installed.

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE NEB maker | unavailable | unavailable | No |

## Purpose: Phonon

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `StaticSetGenerator` | `PhononDisplacementMaker` | partial support | No |

Optional dependency issue: phonon flow modules did not import because
`phonopy` and `seekpath` are not installed in the surveyed environment.

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE phonon maker | unavailable | unavailable | No |

## Purpose: Slab / Adsorption / Molecule

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MPRelaxSet` | `BulkRelaxMaker` | available | No |
| `MPRelaxSet` | `SlabRelaxMaker` | available | No |
| `MPRelaxSet` | `SlabStaticMaker` | available | No |
| `MPRelaxSet` | `MolRelaxMaker` | available | No |
| `MPRelaxSet` | `MolStaticMaker` | available | No |
| composed adsorption flow | `AdsorptionMaker` | available | No |
| `MVLSlabSet` | pymatgen input set only | available | No |
| `MVLGBSet` | pymatgen input set only | available, grain-boundary specific | No |

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE adsorption maker | unavailable | unavailable | No |

## Purpose: GW

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `MVLGWSet` | `MVLStaticMaker` | available | No |
| `MVLGWSet` | `MVLNonSCFMaker` | available | No |
| `MVLGWSet` | `MVLGWMaker` | available | No |
| composed MVL GW band structure flow | `MVLGWBandStructureMaker` | available | No |
| `MVLGWSet` | pymatgen input set | available | No |

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE GW maker | unavailable | unavailable | No |

## Purpose: LOBSTER

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `LobsterTightStaticSetGenerator` | `LobsterStaticMaker` | available | No |
| composed VASP+LOBSTER flow | `VaspLobsterMaker` | partial support | No |

### Theory: r2SCAN / HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN/HSE LOBSTER maker | unavailable | unavailable | No |

## Purpose: AMSET / Transport

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| dense uniform VASP input | `DenseUniformMaker` | available | No |
| static deformation input | `StaticDeformationMaker` | available | No |
| composed VASP AMSET flow | `VaspAmsetMaker` | available | No |
| deformation-potential flow | `DeformationPotentialMaker` | available | No |

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| HSE dense uniform input | `HSEDenseUniformMaker` | available | No |
| HSE static deformation input | `HSEStaticDeformationMaker` | available | No |
| composed HSE AMSET flow | `HSEVaspAmsetMaker` | available | No |

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN AMSET maker | unavailable | unavailable | No |

## Purpose: Electron-Phonon

### Theory: PBE

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| `ElectronPhononSetGenerator` | `SupercellElectronPhononDisplacedStructureMaker` | partial support | No |
| composed electron-phonon flow | `ElectronPhononMaker` | partial support | No |

### Theory: HSE06

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| composed HSE electron-phonon flow | `HSEElectronPhononMaker` | partial support | No |

### Theory: r2SCAN

| InputSet / Generator | Maker | Status | Burton overrides |
|---|---|---|---|
| direct r2SCAN electron-phonon maker | unavailable | unavailable | No |

## Optional Modules Not Fully Available In The Surveyed Environment

The following atomate2 VASP modules exist but did not fully import because
optional dependencies are missing:

| Module | Missing dependency / reason | Status |
|---|---|---|
| `atomate2.vasp.jobs.defect` | `pymatgen.analysis.defects` | partial support |
| `atomate2.vasp.flows.defect` | `pymatgen.analysis.defects` | partial support |
| `atomate2.vasp.flows.electrode` | `pymatgen.analysis.defects` | partial support |
| `atomate2.vasp.flows.approx_neb` | `pymatgen-analysis-diffusion` | partial support |
| `atomate2.vasp.flows.phonons` | `phonopy`, `seekpath` | partial support |
| `atomate2.vasp.flows.gruneisen` | `phonopy` | partial support |
| `atomate2.vasp.flows.qha` | `phonopy` | partial support |

Do not expose these workflows until their optional dependencies are deliberately
added to the BMD Compute environment and tested on PowerSLURM.

## Raw Installed Input-Set Surface

### atomate2 VASP Set Generators

Installed set-generator classes relevant to VASP:

```text
ApproxNebSetGenerator
ChargeStateRelaxSetGenerator
ChargeStateStaticSetGenerator
ElectronPhononSetGenerator
EosSetGenerator
HSEBSSetGenerator
HSEChargeStateRelaxSetGenerator
HSEChargeStateStaticSetGenerator
HSERelaxSetGenerator
HSEStaticSetGenerator
HSETightRelaxSetGenerator
LobsterTightStaticSetGenerator
MDSetGenerator
MPGGAEosRelaxSetGenerator
MPGGAEosStaticSetGenerator
MPLegacyEosRelaxSetGenerator
MPLegacyEosStaticSetGenerator
MPMetaGGAEosPreRelaxSetGenerator
MPMetaGGAEosRelaxSetGenerator
MPMetaGGAEosStaticSetGenerator
MPMorphMDSetGenerator
NebSetGenerator
NonSCFSetGenerator
RelaxConstVolSetGenerator
RelaxSetGenerator
StaticSetGenerator
TightRelaxConstVolSetGenerator
TightRelaxSetGenerator
```

### pymatgen VASP Input Sets

Installed pymatgen VASP input-set classes relevant to BMD Compute:

```text
CINEBSet
DictSet
MITMDSet
MITNEBSet
MITRelaxSet
MP24RelaxSet
MP24StaticSet
MPAbsorptionSet
MPHSEBSSet
MPHSERelaxSet
MPMDSet
MPMetalRelaxSet
MPNMRSet
MPNonSCFSet
MPRelaxSet
MPSOCSet
MPScanRelaxSet
MPScanStaticSet
MPStaticSet
MVLElasticSet
MVLGBSet
MVLGWSet
MVLNPTMDSet
MVLRelax52Set
MVLScanRelaxSet
MVLSlabSet
MatPESStaticSet
NEBSet
VaspInputSet
```

## Recommended Implementation Roadmap

1. Keep current PBE Relax, Relax Ions and Static mappings unchanged.
2. Add native PBE DOS through `NonSCFMaker` / `NonSCFSetGenerator`.
3. Add native PBE Band Structure through atomate2 band-structure flow makers.
4. Add native PBE Dielectric through `DielectricMaker`.
5. Add r2SCAN Relax and Static using MP2024 or MPMetaGGA makers after deciding
   which policy Burton Lab wants to standardize.
6. Add HSE06 Static and Band Structure using atomate2 HSE makers.
7. Treat SOC, DFT+U, spin polarization and gamma-only as modifiers layered on
   upstream makers only where atomate2/pymatgen do not already provide a clear
   abstraction.
8. Defer EOS, elastic, phonon, NEB, GW, LOBSTER, AMSET and electron-phonon
   workflows until the core notebook-replacement path is stable.

