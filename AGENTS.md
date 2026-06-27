\# AGENTS.md



\# BMD Compute



This document describes the philosophy and architecture of the BMD Compute project.



The purpose of this document is to keep both human developers and coding agents aligned as the project evolves.



\---



\# Project Goal



BMD Compute is a browser-based interface for building, submitting, monitoring and retrieving VASP calculations using the existing Atomate2/Jobflow/PowerSLURM backend.



The long-term goal is for BMD Compute to replace the existing Jupyter notebook workflow as the primary user interface.



The notebook remains only as a reference implementation and development aid.



\---



\# Guiding Principles



\## The web interface is the product.



Everything should ultimately serve the browser interface.



Do not develop features exclusively for the notebook unless they are required for backend development.



\---



\## Never duplicate scientific logic.



Scientific functionality should exist in exactly one place.



Avoid situations where both the notebook and the web interface implement the same scientific workflow independently.



Instead:



Notebook

&#x20;       ↓

Backend

&#x20;       ↑

Web Interface



Both interfaces should call the same backend functions.



\---



\## Keep FastAPI thin.



FastAPI should only



\- receive user input

\- validate requests

\- call backend functions

\- return results



FastAPI should NOT contain



\- workflow construction

\- SLURM submission logic

\- pymatgen logic

\- parsing logic

\- scientific calculations



\---



\## The backend owns the science.



Scientific code belongs in backend/.



Examples include



\- structure parsing

\- workflow generation

\- submission

\- monitoring

\- result parsing



These modules should remain usable independently of the web interface.



\---



\# Structure Philosophy



Regardless of input source



\- POSCAR text

\- CIF text

\- uploaded file

\- Materials Project

\- future databases



all inputs should become



pymatgen.Structure



as early as possible.



From that point onward every workflow should operate only on Structure objects.



\---



\# User Interface Philosophy



Expose scientific workflows.



Do NOT expose unnecessary VASP implementation details.



Preferred interface



\- Relaxation

\- Static

\- Band Structure

\- HSE06



Avoid exposing large numbers of INCAR tags unless scientifically justified.



The interface should feel like software written by computational materials scientists rather than generic HPC software.



\---



\# Development Philosophy



Develop incrementally.



Every commit should



\- compile

\- run

\- be testable



Avoid large rewrites.



Each feature should be implemented in small, verifiable steps.



\---



\# Migration Strategy



The existing notebook is the reference implementation.



Features should migrate according to the following pattern:



Notebook feature

&#x20;       ↓

Reusable backend function

&#x20;       ↓

Web interface



Avoid rewriting notebook functionality directly inside FastAPI.



\---



\# Repository Layout



backend/

&#x20;   Scientific logic



templates/

&#x20;   HTML templates



static/

&#x20;   CSS, JavaScript and images



uploads/

&#x20;   Temporary uploaded files



reference/

&#x20;   Reference notebook and supporting design material



\---



\# Long-Term Vision



A user should be able to



1\. Enter a structure.

2\. Choose a workflow.

3\. Submit the workflow.

4\. Monitor progress.

5\. Retrieve results.



without interacting directly with



\- SSH

\- SLURM

\- Atomate2 internals

\- Jobflow internals



The computational infrastructure should remain hidden behind a clean scientific interface.



\---



\# Design Rule



When making architectural decisions, ask:



"Does this move BMD Compute closer to replacing the notebook?"



If the answer is no, reconsider the design.

