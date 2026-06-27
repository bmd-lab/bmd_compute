\# Reference Material



This directory contains historical material used during the development of BMD Compute.



These files are \*\*not\*\* part of the application.



They are preserved because they represent validated implementations of the underlying scientific workflows.



\## workflow-selector-macandwindows.ipynb



This notebook served as the primary interface for Atomate2/Jobflow/PowerSLURM before the development of BMD Compute.



The notebook is considered the reference implementation of the workflow logic.



When migrating functionality into BMD Compute:



\- preserve scientific behaviour

\- refactor into reusable backend modules

\- avoid rewriting working logic without justification



The long-term goal is for BMD Compute to replace the notebook as the primary user interface while maintaining the same validated scientific workflows.

