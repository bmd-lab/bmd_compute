# BMD Compute

BMD Compute is a browser-based on-ramp for Burton Materials Design Lab students and beginning computational users to build, submit, monitor, and inspect VASP calculations on TAU PowerSLURM.

It is intentionally not a replacement for normal SSH/SLURM cluster access for experienced computational researchers. Its job is to make the first scientific workflow visible and teachable: structure in, reviewed calculation plan, generated VASP inputs, remote submission, monitoring, and scientific results.

## Architecture

```text
Browser
  -> FastAPI / Jinja
  -> stage-first WorkflowSpec
  -> pymatgen / atomate2 / jobflow
  -> Paramiko
  -> PowerSLURM
  -> VASP
  -> remote pymatgen result parsing
  -> Results UI
```

FastAPI should stay a thin controller. Scientific behavior belongs in `backend/`; infrastructure concerns such as SSH, SLURM submission, provenance, and remote result parsing are kept separate from calculation policy where practical.

## Current Capabilities

Supported student-facing workflows include:

- Geometry Optimisation
- Static Energy
- Double Geometry Optimisation
- Geometry Optimisation -> Static Energy
- Density of States
- Band Structure

Supported scientific controls include:

- PBE
- HSE06 for reviewed stages and stage-first workflows
- Spin Polarised calculations where enabled by the stage registry
- explicit DFT+U, only when the selected input set has reviewed U values
- SOC for reviewed PBE Static Energy stages and compatible static-to-SOC workflows

Validated or specifically reviewed examples include PBE Static + SOC on Si, PBE + DFT+U Static -> PBE + DFT+U + SOC Static on Fe2O3, and HSE06 Band Structure through the stage-first HSE static precursor path.

Deliberately unavailable or unvalidated combinations remain blocked by validation. Examples include r2SCAN, Dielectric, GW, HSE06 DOS, HSE06 + SOC, general SOC relaxation, and arbitrary free-form INCAR editing.

## Scientific Policy Notes

BMD Compute uses pymatgen and atomate2 as the default scientific implementation layer and applies small Burton Lab policies centrally.

Important current policies:

- Generated inputs are pre-submission policy previews. In downstream stages after relaxation, the runtime structure comes from the previous completed stage.
- PBE relax stages use the Burton Lab relax policy, including ENCUT 580 eV.
- Static/final electronic stages use the Burton Lab final policy, including ENCUT 620 eV where applicable.
- HSE06 policy is stage-specific: relax uses `PRECFOCK = Fast`, static uses `PRECFOCK = Accurate`, and HSE06 band structure uses the reviewed atomate2 HSE band path.
- DFT+U is explicit. BMD Compute does not silently inherit Hubbard U into plain PBE calculations.
- SOC/non-collinear stages route to `vasp_ncl`, suppress incompatible `LELF`, omit `ISPIN`, and preserve vector `MAGMOM` initialization.
- NCORE is an execution-resource policy, not a theory policy. Automatic NCORE is stage-specific and currently applies to Relax, Static, and DOS stages; Band Structure omits automatic NCORE pending separate benchmarking.

## Operational Safety

The current deployment model is:

```text
authorized student
  -> TAU VPN / university network
  -> BMD Compute
  -> constrained shared bmdguest identity
  -> PowerSLURM
```

There is no application-level login, SSO, CSRF protection, or per-user job ownership boundary in the current app. That is an accepted VPN-bound lab deployment policy, not a public Internet security model. Do not expose the Uvicorn port directly to the public Internet. Resume by SLURM job ID is a convenience for shared service/group calculations, not a private authorization boundary.

Current operational hardening includes allow-listed CPU/memory/queue values, fixed backend account policy, bounded process-local SSH concurrency, deterministic SSH cleanup, server-side submission idempotency, explicit Load Results for completed calculations, remote-side pymatgen result parsing, production traceback hiding, and structured submission provenance.

## Development

The environment file is intentionally broad and currently not a lock file. It describes the main conda packages needed by the app, but exact production reproducibility still depends on the maintained `bmd-compute` environment.

Typical local checks:

```bash
python -m pytest tests -q
python -m compileall backend tests
git diff --check
```

On the Windows/Codex development machine, use the intended environment, for example:

```bash
conda run -n bmd-compute python -m pytest tests -q
```

