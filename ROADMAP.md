# BMD Compute Roadmap

The roadmap is organized around complete scientific capabilities rather than technical tasks.

Each completed phase should leave BMD Compute in a usable state.

---

# Phase 0 — Project Foundation ✅

- [x] FastAPI project
- [x] Git repository
- [x] GitHub repository
- [x] SSH authentication
- [x] Project structure
- [x] AGENTS.md
- [x] ROADMAP.md
- [x] Reference notebook
- [x] Basic web interface

---

# Phase 1 — Structure Analysis ✅

Goal:
Accept crystallographic input through the browser and present a scientific summary.

- [x] Paste POSCAR
- [x] Parse structure
- [x] Structure summary backend
- [x] Browser ↔ FastAPI ↔ Backend integration
- [x] Formula
- [x] Reduced formula
- [x] Number of atoms
- [x] Volume
- [x] Density
- [x] Lattice parameters
- [x] Lattice angles
- [x] Space group
- [x] Crystal system

---

# Phase 2 — Workflow Construction

Goal:
Build Atomate2 workflows from browser input without submitting them.

- [ ] Workflow selector
- [ ] Workflow summary
- [ ] Build RelaxMaker flow
- [ ] Build StaticMaker flow
- [ ] Dry run
- [ ] Display workflow information
- [ ] Display generated job graph

---

# Phase 3 — Remote Submission

Goal:
Replace the notebook's submission workflow.

- [ ] Submit workflow
- [ ] Remote runner
- [ ] PowerSLURM submission
- [ ] Receive Job ID
- [ ] Store JobRecord

---

# Phase 4 — Monitoring

Goal:
Replace notebook monitoring.

- [ ] Queue status
- [ ] Running status
- [ ] Live job information
- [ ] Error reporting
- [ ] Job history

---

# Phase 5 — Results

Goal:
Replace notebook parsing and visualization.

- [ ] Parse completed calculations
- [ ] Structure viewer
- [ ] Download outputs
- [ ] Calculation summary
- [ ] Results page

---

# Phase 6 — Scientific Workflows

Goal:
Expand supported workflows.

- [ ] Relax → Static
- [ ] Band Structure
- [ ] DOS
- [ ] HSE06
- [ ] Additional Atomate2 workflows

---

# Phase 7 — Deployment

Goal:
Deploy BMD Compute as the primary interface.

- [ ] Authentication
- [ ] Multi-user support
- [ ] Persistent application database
- [ ] Production deployment
- [ ] Integration with BMD Lab website
