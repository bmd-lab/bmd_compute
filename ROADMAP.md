# BMD Compute Roadmap

The roadmap is organized around complete scientific capabilities rather than
technical tasks. Each completed phase should leave BMD Compute in a usable
state.

---

# Phase 0 - Project Foundation - Done

- [x] FastAPI project
- [x] Git repository
- [x] GitHub repository
- [x] SSH authentication path
- [x] Project structure
- [x] AGENTS.md
- [x] ROADMAP.md
- [x] Reference notebook
- [x] Basic web interface

---

# Phase 1 - Structure Analysis - Done

Goal: accept crystallographic input through the browser and present a
scientific summary.

- [x] Paste POSCAR
- [x] Paste CIF
- [x] Parse structure
- [x] Structure summary backend
- [x] Browser to FastAPI to backend integration
- [x] Formula
- [x] Reduced formula
- [x] Number of atoms
- [x] Volume
- [x] Density
- [x] Lattice parameters
- [x] Lattice angles
- [x] Space group
- [x] Crystal system
- [x] User-facing validation errors for malformed POSCAR input

---

# Phase 2 - Calculation Construction - Done

Goal: replace notebook workflow construction while remaining entirely local.

Backend:

- [x] Extract workflow construction from notebook
- [x] Introduce `CalculationSpec`
- [x] Build PBE static Atomate2 Flow
- [x] Build PBE relaxation Atomate2 Flow
- [x] Support spin-polarized modifier for implemented PBE workflows
- [x] Generate VASP input previews without reading POTCAR files locally
- [x] Backend smoke tests

Browser:

- [x] Progressive calculation section
- [x] Calculation selector
- [x] Build Calculation button
- [x] Scientific summary
- [x] Generated INCAR, KPOINTS, and POSCAR previews
- [ ] Display generated Jobflow graph

---

# Phase 3 - Remote Preparation And Submission - First Pass Done

Goal: replace notebook submission.

- [x] SubmissionSpec
- [x] RemoteRunner abstraction
- [x] Paramiko-backed remote runner
- [x] Verified remote preparation dry run
- [x] Upload remote execution package
- [x] Write sbatch script
- [x] PowerSLURM submission
- [x] Receive SLURM job ID
- [x] Persist best-effort remote JobRecord
- [ ] Harden real-cluster error handling based on more production failures
- [ ] Add cancellation when needed

---

# Phase 4 - Monitoring - First Pass Done

Goal: replace notebook monitoring.

- [x] Query `squeue`
- [x] Query `scontrol`
- [x] Query `sacct` for completed jobs
- [x] Running, pending, success, failure classification
- [x] Resume existing calculation by SLURM job ID
- [x] Browser refresh flow
- [x] Error reporting for malformed IDs, SSH failures, and scheduler failures
- [ ] Job history
- [ ] Background/live watcher

---

# Phase 5 - Results - In Progress

Goal: replace notebook parsing and visualization.

- [x] Detect completed successful calculations
- [x] Resolve BMD run directory from submission state or remote JobRecord
- [x] Read CONTCAR, OUTCAR, and vasprun.xml
- [x] Parse final energy, energy per atom, convergence, and final formula
- [x] Final structure viewer
- [x] Results diagnostics
- [ ] Download outputs
- [ ] Dedicated results page or richer results panel
- [ ] Additional result visualizations

---

# Phase 6 - Scientific Workflows

Goal: expand supported Atomate2 workflows.

- [x] PBE static
- [x] PBE relaxation
- [x] PBE spin-polarized static and relaxation
- [ ] Relax to static
- [ ] Density of States
- [ ] Band Structure
- [ ] Dielectric
- [ ] r2SCAN
- [ ] HSE06
- [ ] Additional Atomate2 workflows

---

# Phase 7 - Deployment

Goal: deploy BMD Compute as the primary interface.

- [ ] Authentication
- [ ] Multi-user support
- [ ] Persistent application database
- [ ] Production deployment
- [ ] Integration with BMD Lab website
