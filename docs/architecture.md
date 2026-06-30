# BMD Compute Architecture

This document describes the high-level architecture of BMD Compute.

The purpose of this document is to explain how the application is organized, how information flows through the system, and where future functionality should be added.

---

# Project Goal

BMD Compute is a browser-based scientific application for constructing, submitting, monitoring and retrieving Atomate2/VASP workflows.

The web interface replaces the previous Jupyter notebook while preserving its validated scientific behaviour.

---

# Overall Architecture

```
Browser
    │
    ▼
FastAPI
    │
    ▼
Backend
    │
    ▼
RemoteRunner
    │
    ▼
PowerSLURM
    │
    ▼
VASP
```

The browser should never communicate directly with the cluster.

All scientific behaviour is delegated to backend modules.

FastAPI serves only as the controller between the browser and the backend.

---

# Backend Architecture

```
backend/

parser.py
summary.py
workflows.py
workflow_summary.py
submission.py
remote.py
```

Each module owns one scientific concept.

---

# Data Flow

The application progresses through a sequence of increasingly rich scientific objects.

```
Structure
        │
        ▼
StructureSummary
        │
        ▼
Workflow
        │
        ▼
WorkflowSummary
        │
        ▼
SubmissionSpec
        │
        ▼
RemoteRunner
        │
        ▼
JobRecord
        │
        ▼
ResultSummary
```

Every stage adds information without discarding previous state.

---

# Responsibilities

## Browser

Responsible for

* collecting user input
* displaying scientific information
* progressively revealing workflow stages

The browser contains no scientific logic.

---

## FastAPI

Responsible for

* receiving requests
* validating inputs
* calling backend modules
* rendering templates

FastAPI should remain intentionally thin.

---

## Backend

Responsible for

* structure parsing
* structure summaries
* Atomate2 workflow construction
* workflow summaries
* submission preparation
* remote execution abstraction
* result parsing

Backend modules should remain usable independently of FastAPI.

---

## RemoteRunner

RemoteRunner abstracts every interaction with the remote cluster.

FastAPI should never interact directly with

* SSH
* Paramiko
* sbatch
* SLURM commands

Instead it should call a RemoteRunner implementation.

Current state:

```
RemoteRunner
```

Future implementation:

```
ParamikoRemoteRunner
```

---

# Progressive Workflow

BMD Compute is designed as a scientific workbench rather than a traditional website.

The interface progressively reveals additional functionality.

```
Structure

↓

Structure Summary

↓

Workflow Selection

↓

Workflow Summary

↓

Submission Preview

↓

Submission

↓

Monitoring

↓

Results
```

The user should remain on a single page whenever practical.

Previous scientific context should remain visible.

---

# Migration Strategy

The reference notebook remains the scientific specification.

Migration follows this pattern:

```
Notebook behaviour

↓

Backend extraction

↓

Browser integration
```

Notebook code should not be copied directly into FastAPI.

Scientific behaviour should be extracted into reusable backend modules.

---

# Current Status

Implemented

* Structure parsing
* Structure summaries
* Atomate2 workflow construction
* Workflow summaries
* Submission specification
* RemoteRunner abstraction
* Progressive browser interface

Remaining

* Paramiko RemoteRunner implementation
* PowerSLURM submission
* Monitoring
* Result parsing
* Structure visualization
* Browser deployment

---

# Design Philosophy

The browser should speak the language of materials scientists.

The backend should speak the language of Atomate2.

The infrastructure should speak the language of HPC.

Each layer should remain largely unaware of implementation details in the layers beneath it.

For example

```
Browser

"Relaxation"
```

should eventually become

```
Atomate2

RelaxMaker
```

which eventually becomes

```
PowerSLURM

sbatch
```

without the browser needing to know how any of those transitions occur.

---

# Long-Term Vision

A researcher should be able to

1. Enter a structure.
2. Verify the structure.
3. Build a workflow.
4. Review the submission.
5. Submit the calculation.
6. Monitor progress.
7. Retrieve results.

without interacting directly with

* SSH
* SLURM
* shell scripts
* Atomate2 internals

The browser should become the primary interface for computational materials research within the Burton Materials Design Lab.

