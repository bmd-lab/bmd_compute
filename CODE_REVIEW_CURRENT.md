# BMD Compute Current Code Review

Date: 2026-08-18

Scope: independent review of the current repository tree, including the reference notebook, AGENTS.md architecture guidance, backend modules, FastAPI routes, templates, tests, setup files, and project documentation. This review is audit-only. No VASP or SLURM jobs were submitted.

Severity labels:

- CRITICAL: likely to corrupt science, leak credentials, or block all production use.
- HIGH: important production-readiness or correctness issue before wider student use.
- MEDIUM: material risk or maintainability debt that should be scheduled.
- LOW: cleanup, documentation, or localized hardening.
- INFO: verified status or context, not an action item by itself.

Evidence labels:

- VERIFIED: directly observed in current source/tests.
- INFERRED: reasoned from current implementation but not triggered in a live job during this review.
- POLICY: current behavior appears intentional and should be treated as a policy decision.

## 1. Executive Summary

BMD Compute has improved substantially since the earlier review state. The stage-first model is now the clear center of gravity, and the scientific policies that have recently been validated on PowerSLURM are encoded in focused backend layers rather than spread through the UI. In particular, PBE/HSE06 stage composition, HSE06 band-structure generation, SOC non-collinear INCAR policy, VASP executable routing, SSH config resolution, SFTP-based preparation, remote-side result parsing, and SSH cleanup all have real tests behind them.

No CRITICAL issue was found in the reviewed tree. The biggest remaining risks are production-readiness issues rather than immediate scientific-policy failures:

| Priority | Finding | Severity | Evidence |
| --- | --- | --- | --- |
| Fix immediately | `/monitor` still synchronously loads completed results and can occupy a request for up to the remote parser timeout. | HIGH | VERIFIED |
| Before wider student use | No authentication, authorization, CSRF protection, or server-side job ownership model is visible in the app. | HIGH | VERIFIED |
| Before wider student use | SSH lifecycle leaks are covered, but legitimate concurrent requests are still unbounded. | MEDIUM/HIGH | VERIFIED |
| Before production | Paramiko accepts unknown host keys with `AutoAddPolicy`. | MEDIUM/HIGH | VERIFIED |
| Near-term | Documentation and environment files are stale relative to current HSE06, SOC, DOS, Band Structure, memory dropdown, and remote execution behavior. | MEDIUM | VERIFIED |

Overall recommendation: keep the scientific workflow architecture stable and spend the next pass on operational hardening: make completed-result loading explicitly user-triggered everywhere, add bounded remote-operation concurrency, define the production auth model, and update deployment/onboarding docs.

## 1A. Post-Review Status Update - 2026-08-19

This section reconciles the original 2026-08-18 review against the current repository after the operational-hardening follow-up work. The original findings remain below for historical context.

Resolved or substantially reduced:

- `/monitor` is now monitoring-only. Refresh Queue Status queries scheduler state and returns `results_summary=None`; completed result parsing is reached through the explicit Load Results action.
- Legitimate concurrent SSH-backed requests are bounded inside a single BMD Compute process by `RemoteOperationLimiter`, with defaults `BMD_MAX_CONCURRENT_REMOTE_OPERATIONS=4` and `BMD_REMOTE_OPERATION_SLOT_TIMEOUT_S=5`.
- Submission idempotency is implemented with remote-side submission-attempt state and locking, so repeated submit requests with the same attempt id should not create duplicate SLURM jobs.
- Production traceback exposure is controlled by debug mode. The normal production path should show user-facing errors rather than raw Python tracebacks.
- Submission provenance is recorded in `submission.json`, including schema version, Git/source state when available, local scientific package versions, workflow/resources, POTCAR identity metadata, and per-stage VASP executable policy.
- Documentation now describes the current stage-first, HSE06, SOC, DOS, Band Structure, monitoring, result-loading, resource, and deployment behavior.

Accepted deployment policy, not an accidental omission:

- The current access model is VPN/university-network bound use of BMD Compute by authorized students through a constrained shared `bmdguest` identity on PowerSLURM.
- There is still no app-level authentication, SSO, CSRF protection, or private per-user job ownership boundary. That is acceptable only for the current VPN-bound lab service. Public exposure without authentication remains unsafe.
- Resume by Job ID is a shared-service recovery feature, not an ownership or privacy boundary.

Still future or still worth tracking:

- Paramiko host-key policy should be reviewed before broader production exposure.
- A durable job database/history is still future work.
- The SSH concurrency limit and results cache are process-local; multiple Uvicorn workers need a cross-process design if used.
- `main.py` remains thicker than the long-term FastAPI-thin-controller target.
- Exact dependency locking remains future work; `environment.yml` is setup metadata, not a reproducibility lock file.

## 2. What Has Improved Since Previous Review

Major previous problem areas are now fixed or sharply reduced:

- Stage-first workflow representation is now real. `StageSpec` and `WorkflowSpec` exist in `backend/calculations/models.py:104`, and validation enforces stage order and compatible precursors in `backend/calculations/registry.py:313`.
- HSE06 policy is centralized by theory plus stage in `backend/calculations/theory_policy.py:46`, including `PRECFOCK=Fast` for relax, `PRECFOCK=Accurate` and `ISMEAR=0` for static, and HSE band-stage overrides.
- HSE06 Band Structure now uses atomate2's hybrid band primitives: `HSEBSSetGenerator` in `backend/workflows.py:1234` and `HSEBSMaker` in `backend/workflows.py:1561`.
- The previous VASP launch-command regression is fixed. `resolve_vasp_cmd_argv()` expands `$SLURM_NTASKS` using runtime environment/spec values in `backend/execution.py:58`, and `configure_atomate2_vasp_command()` sets the command consumed by atomate2/custodian in `backend/execution.py:87`.
- Large generated inputs are now uploaded as file data through SFTP. The active preparation path returns file groups from `backend/submission.py:497`, and Paramiko writes them with `put_text()` via SFTP in `backend/paramiko_remote.py:621`.
- SSH connection lifetime has been audited and covered by tests. `connected_remote_runner()` deterministically closes the runner in `backend/remote_runtime.py:44`, and `tests/test_remote_lifecycle.py` covers success, failure, SFTP exceptions, timeout, sequential, and concurrent cleanup cases.
- SOC non-collinear policy now suppresses invalid ELF output, omits `ISPIN`, routes to `vasp_ncl`, and converts scalar/site MAGMOM to vector form. The core SOC composition is in `backend/workflows.py:298`, `backend/workflows.py:441`, and `backend/workflows.py:595`, with representative preview/runtime tests in `tests/test_generated_inputs.py:155` and `tests/test_generated_inputs.py:200`.
- The PBE Static -> SOC WAVECAR transition has been disabled after real cluster validation showed the copied WAVECAR is not reliable for that transition. `wavecar_carry_forward_transition()` currently returns `False` in `backend/workflows.py:261`.
- DOS and Band Structure workflow results are parsed remotely with pymatgen rather than transferring raw `vasprun.xml`. The remote parser source and narrow JSON context are constructed in `backend/results.py:576` and `backend/results.py:635`.
- Plotly visualizations have workflow-specific payloads, high-symmetry label handling, spin-aware legends, default band y-window, and PNG download controls. See `backend/remote_result_parser.py:318`, `backend/remote_result_parser.py:545`, `templates/index.html:2097`, and tests in `tests/test_workflow_results.py`.
- The account field has been removed from user-editable resources, and memory is now a backend-defined dropdown. `ALLOWED_MEMORY_GB` is defined in `backend/calculations/resources.py:11` and rendered in `templates/index.html:1101`.

## 3. Scientific Correctness

### Finding SC-1: Current validated scientific policies are encoded in central layers

Severity: INFO  
Evidence: VERIFIED

The current scientific policy surface is much healthier than the old ad hoc path. HSE06 functional and stage-specific INCAR settings are centralized in `backend/calculations/theory_policy.py:46`. Resource-derived NCORE eligibility is centralized separately in `backend/calculations/resources.py:14`, which correctly keeps NCORE out of theory policy. SOC settings are composed in `backend/workflows.py:298` and include the important validated settings: `LSORBIT`, `LNONCOLLINEAR`, `ISYM=0`, `GGA_COMPAT=False`, `SAXIS`, `LELF=None`, and `ISPIN=None`.

Impact: low immediate risk. The current code now reflects the validated PBE, HSE06, DOS, Band Structure, and SOC lessons instead of leaving them as UI or workflow-builder special cases.

Direction: keep new scientific changes in these policy layers. Avoid reopening the old pattern of patching INCAR details in route handlers or templates.

### Finding SC-2: HSE06 Band Structure is enabled only through stage-first workflows, not legacy global purpose

Severity: INFO  
Evidence: VERIFIED / POLICY

The global `CalculationSpec(Purpose.BAND_STRUCTURE, Theory.HSE06)` remains unsupported in the compatibility matrix, while a stage-first sequence such as PBE relax -> HSE06 static -> HSE06 band is validated by `validate_workflow_spec()`. The tests explicitly preserve this distinction in `tests/test_hse06_band_structure_workflow.py:237` and `tests/test_hse06_band_structure_workflow.py:246`.

Impact: good. This prevents users from selecting a scientifically ambiguous HSE06 Band Structure workflow without the required HSE06 electronic precursor.

Direction: document this distinction clearly in the UI/docs. The user chooses scientific stages; the legacy single-purpose compatibility layer should continue to be treated as a compatibility shell, not the future model.

### Finding SC-3: SOC support is properly narrow, but documentation should say so

Severity: LOW  
Evidence: VERIFIED / POLICY

SOC is supported for PBE Static stages and blocked elsewhere by the stage modifier matrix. `_supported_modifiers_for_stage()` allows SOC for PBE static in `backend/calculations/registry.py:626`, while HSE06 stages only support the single-stage modifiers in `backend/calculations/registry.py:636`.

Impact: scientifically appropriate for the current validation state. The risk is not the code path; the risk is users or maintainers assuming SOC is more general than it is.

Direction: state the supported SOC scope in README/docs: currently reviewed for PBE Static and composed workflows ending in PBE Static + SOC; not reviewed for HSE06 + SOC or general SOC relaxation.

### Finding SC-4: The HSEBS `auto_nbands` custodian handling is a narrow local policy

Severity: INFO  
Evidence: VERIFIED / POLICY

`backend/calculations/custodian_policy.py:6` excludes `auto_nbands` from the relevant VaspErrorHandler only for HSE Band Structure. This is applied in the HSEBSMaker path in `backend/workflows.py:1565`. Tests assert that HSEBS custodian policy allows `auto_nbands` in `tests/test_hse06_band_structure_workflow.py:221`.

Impact: this matches the live finding that VASP may increase NBANDS to a parallel-compatible value and continue successfully. The current solution does not disable the handler globally.

Direction: keep this as an explicitly documented policy until the deployed custodian version natively treats `auto_nbands` as non-fatal.

## 4. Preview vs Execution

### Finding PV-1: Preview and runtime now share the same stage builders for policy-sensitive inputs

Severity: INFO  
Evidence: VERIFIED

Generated previews are built through `preview_generated_inputs()` in `backend/generated_inputs.py:36`, which delegates to the same workflow builders used for execution. SOC preview/runtime MAGMOM consistency is covered by direct regression tests in `tests/test_generated_inputs.py:200` for Si and `tests/test_generated_inputs.py:346` for Fe12O18.

Impact: the previous preview/runtime SOC MAGMOM mismatch appears fixed.

Direction: continue requiring preview/runtime comparison tests for every new modifier or stage-local theory amendment.

### Finding PV-2: Downstream stage POSCAR previews cannot show post-relaxation structures before runtime

Severity: LOW  
Evidence: VERIFIED / POLICY

`_preview_workflow_inputs()` previews every stage from the original submitted structure in `backend/generated_inputs.py:109`, while runtime stage chaining uses previous job outputs and `prev_dir` in `backend/workflows.py:1446`. This is scientifically unavoidable before a relaxation has run, but it can be misunderstood.

Impact: users may read a Stage 2 POSCAR preview as the literal final runtime structure. For relax -> static workflows, the actual static POSCAR will be generated from the relaxed Stage 1 output.

Direction: keep behavior unchanged, but label generated inputs as "pre-submission input policy preview" or add small text for downstream stages: "Runtime structure for this stage comes from the previous completed stage."

### Finding PV-3: RESOLVED - displayed SLURM script differed from the submitted script

Severity: RESOLVED
Evidence: VERIFIED

The former `build_slurm_preview_script()` maintained a separate conceptual script that ended in a direct VASP command, while remote preparation uploaded the script from `build_sbatch_script()`, which runs `run_job.py`.

Resolution: `build_sbatch_script()` is now the sole script-text producer. The UI and remote preparation consume the same deterministic artifact, and submission provenance records its SHA-256 digest and UTF-8 byte size.

## 5. Workflow / Jobflow Execution

### Finding WF-1: Linear stage execution is well covered, but dynamic jobflow semantics are not fully general

Severity: MEDIUM  
Evidence: INFERRED

The local stage-directory runner in `_run_locally_with_stage_directories()` executes `current_flow.iterflow()` and maps jobs by index to stage directories in `backend/execution.py:182`. It recursively handles `response.replace`, `response.detour`, and `response.addition` in `backend/execution.py:258`, but nested flows restart their own stage indexing.

Impact: current supported workflows are linear VASP stage chains, so this is not an observed bug. If future atomate2 makers emit dynamic detours/additions that should map to distinct stage directories, directory reuse or confusing stage ownership is possible.

Direction: before adding workflows with non-linear jobflow behavior, add tests with fake detour/addition flows and decide whether dynamic jobs need separate diagnostic directories or should stay inside the parent stage.

### Finding WF-2: Server-side submit idempotency is not enforced

Severity: MEDIUM  
Evidence: VERIFIED

The submit route rebuilds the submission state and calls `submit_remote_workflow()` if the hidden `remote_prepared` field is true in `main.py:744`. There is no persistent server-side job record or idempotency token guarding duplicate POSTs. The run name is timestamp-derived in `backend/submission.py:881`.

Impact: browser double-submits, retries, or scripted POSTs can submit duplicate SLURM jobs under the lab account. The UI may discourage this, but the server does not enforce it.

Direction: add a persistent `JobRecord` or idempotency key before wider student use. The backend already has enough serialized state to compute a submission fingerprint.

### Finding WF-3: FastAPI remains thicker than the stated architecture target

Severity: MEDIUM  
Evidence: VERIFIED

AGENTS.md says FastAPI should receive requests, validate inputs, call backend functions, and return responses. `main.py` currently owns substantial form/state orchestration: `workflow_spec_from_form()` at `main.py:392`, `execution_resources_from_form()` at `main.py:428`, `build_submission_state()` at `main.py:445`, and repeated route-level rebuild/prepare/submit/monitor flows.

Impact: not an immediate correctness bug, but it increases risk as UI state, monitor state, results state, and submission state grow.

Direction: extract a backend application-service layer for `build_submission_state`, `prepare`, `submit`, `monitor`, and `load_results` orchestration while keeping FastAPI as a thin adapter.

## 6. SLURM / Remote Execution

### Finding RE-1: VASP parallel launch source of truth is fixed

Severity: INFO  
Evidence: VERIFIED

`backend/config.py:16` defines the default command as `mpirun -n $SLURM_NTASKS vasp_std`. Runtime expansion happens in `backend/execution.py:58`, using the actual environment first and submission resources as fallback. Tests in `tests/test_execution.py:51` and `tests/test_execution.py:73` verify that `SLURM_NTASKS=24` becomes an argv equivalent to `["mpirun", "-n", "24", "vasp_std"]`.

Impact: the previous `vasp_cmd: ["vasp_std"]` custodian regression appears fixed.

Direction: keep VASP command handling in `backend/execution.py`; do not add independent command composition in workflow builders or templates.

### Finding RE-2: SSH config resolution and Windows/OpenSSH behavior are much improved

Severity: INFO  
Evidence: VERIFIED

`backend/paramiko_remote.py` resolves OpenSSH config through `ssh -G` and Paramiko config fallback in `_resolve_ssh_config()` at `backend/paramiko_remote.py:152`, with platform-independent config discovery in `backend/paramiko_remote.py:300`. Diagnostics include loaded config files, host alias presence, hostname, username, port, identity files, and proxy command in `backend/paramiko_remote.py:121`.

Impact: this addresses the earlier Windows alias/HostName failure without spreading Windows-specific branches through the codebase.

Direction: keep OpenSSH config as the authoritative source.

### Finding RE-3: Remote preparation no longer embeds generated file contents in shell command arguments

Severity: INFO  
Evidence: VERIFIED

The active remote preparation path obtains file groups from `remote_preparation_file_groups()` in `backend/submission.py:497` and uploads each file with Paramiko SFTP `put_text()` in `backend/paramiko_remote.py:621`. This preserves large explicit KPOINTS files as file data rather than as `/bin/bash` command arguments.

Impact: the HSE06 Band Structure large-KPOINTS `/bin/bash: Argument list too long` failure is fixed in the active path.

Direction: remove or quarantine legacy helpers that still construct shell heredoc submission commands, described in RE-6.

### Finding RE-4: Paramiko host-key policy is unsafe for production

Severity: MEDIUM/HIGH  
Evidence: VERIFIED

`ParamikoRemoteRunner.connect()` calls `client.set_missing_host_key_policy(paramiko.AutoAddPolicy())` in `backend/paramiko_remote.py:421`.

Impact: the application will trust the first host key it sees. On a campus or shared network, this weakens SSH's protection against man-in-the-middle attacks.

Direction: switch to known-hosts verification for production. If local development needs relaxed behavior, gate it explicitly by environment/profile and make the production default strict.

### Finding RE-5: Connection cleanup is covered, but remote-operation concurrency remains unbounded

Severity: MEDIUM/HIGH  
Evidence: VERIFIED

Every reviewed UI-triggered remote operation uses `connected_remote_runner()` or equivalent per-operation lifecycle cleanup: prepare in `backend/remote_preparation.py:57`, submit in `backend/remote_submission.py:29`, results in `backend/results.py:110`, and query operations inside Paramiko. Tests in `tests/test_remote_lifecycle.py` verify cleanup under success, failure, timeout, repeated, and concurrent operation scenarios.

However, no bounded concurrency mechanism is visible around remote operations. A burst of legitimate requests can still create many simultaneous SSH sessions. This is separate from the earlier leak problem.

Impact: the multi-user incident could recur as "too many legitimate concurrent SSH connections" even if leaks are fixed. The login node and single Uvicorn process can still be saturated by student clicks.

Direction: add a small bounded remote-operation semaphore/queue after cleanup correctness. Keep it separate from pooling. Report queue/backpressure clearly in the UI.

### Finding RE-6: A legacy shell-heredoc preparation helper remains in the codebase

Severity: LOW  
Evidence: VERIFIED

`build_submission_command()` still exists in `backend/submission.py:588` and `_build_verified_dry_run_command()` builds shell heredocs later in the same module. The active Paramiko preparation path uses SFTP file groups instead.

Impact: this is likely dead or compatibility code, not the active bug path. But if reused, it could reintroduce command-size and shell-quoting risks that SFTP preparation was designed to remove.

Direction: delete it if unused, or mark it test-only/deprecated and ensure no production path can call it for generated inputs.

## 7. Multi-user / Concurrency

### Finding MU-1: No visible auth, ownership, quota, or CSRF boundary

Severity: HIGH  
Evidence: VERIFIED

No authentication middleware, user dependency, session/cookie ownership model, or CSRF mechanism is visible in `main.py`. The UI has stateful POST forms such as Prepare Remote, Submit, Monitor, and Resume in `templates/index.html:1600` and `templates/index.html:1827`, but no CSRF token. ROADMAP still lists Authentication as incomplete in `ROADMAP.md:142`.

Impact: if exposed through the BMD Lab website without an upstream protective layer, any reachable user could submit under the fixed lab account, refresh or resume jobs by ID, and potentially trigger remote operations. Cross-site form posts could also submit jobs.

Direction: before wider student use, define the access boundary. Options include TAU SSO/reverse proxy auth plus per-user server-side job ownership, or an application-level auth/session model. Add CSRF protection for browser forms unless the deployment architecture makes it unnecessary.

### Finding MU-2: Resume by job ID needs ownership rules before broad deployment

Severity: HIGH  
Evidence: VERIFIED / INFERRED

`/resume` accepts a raw `job_id` and calls `monitor_job(job_id)` in `main.py:549`. If the job is completed and Load Results is requested, results discovery reads the BMD remote job-state record from `backend/results.py:256`.

Impact: in an unauthenticated deployment, users can probe job IDs and potentially load results for calculations they did not submit, depending on remote filesystem permissions and job-state visibility.

Direction: associate submitted jobs with authenticated users or session IDs. Require ownership or explicit share tokens for resume/load-results.

### Finding MU-3: The in-memory result cache is per-process and not an ownership boundary

Severity: MEDIUM  
Evidence: VERIFIED

Completed results are cached in process globals in `backend/results.py:42` and keyed by job/run path in `backend/results.py:230`. The cache is bounded to four entries, which is good for memory, but it is not persistent, not shared across workers, and not user-aware.

Impact: multiple workers will not share cache hits. A future auth layer must not rely on this cache for access control.

Direction: keep the cache as an optimization only. Store ownership and durable job metadata separately.

## 8. Performance

### Finding PF-1: `/monitor` can still synchronously parse completed results

Severity: HIGH  
Evidence: VERIFIED

Resume has been decoupled: `resume_existing_calculation()` only calls `load_results_for_completed_job()` when `load_results=true` in `main.py:549`, and the template presents a `Load Results` button in `templates/index.html:1831`. That is good.

The regular Refresh Queue Status route still does this:

- `monitoring_result = monitor_job(...)` in `main.py:945`.
- If success, `results_summary = load_results_for_completed_job(...)` in `main.py:948`.
- Remote parsing can run for up to `REMOTE_RESULT_PARSE_TIMEOUT_S = 900` seconds in `backend/results.py:42` and `backend/results.py:533`.

Impact: a completed job refresh can block a Uvicorn request/thread for many minutes. This undermines the recent monitor-latency refactor and can make the UI appear hung during result parsing.

Direction: make result loading explicit everywhere. Refresh Queue Status should return monitoring state and a Load Results control; Load Results should do remote parsing. This matches the successful Resume behavior.

### Finding PF-2: Result parsing is remote and compact, but still synchronous inside the request

Severity: MEDIUM  
Evidence: VERIFIED

`extract_remote_vasp_result()` runs the parser on the cluster through `runner.run_python()` in `backend/results.py:522`, then returns compact JSON. This avoids raw `vasprun.xml` transfer. The cost is still paid synchronously by the web request that invokes Load Results.

Impact: explicit Load Results can legitimately take time for large outputs. That is acceptable if the UI makes it explicit, but the server thread is still occupied.

Direction: after explicit loading is consistent, consider a bounded background result-parse task with polling if real parse times are too long.

### Finding PF-3: Remote parser source is sent as an SSH heredoc

Severity: LOW  
Evidence: VERIFIED / INFERRED

`remote_result_parser_source()` embeds the current parser source and context into a Python script string in `backend/results.py:576`, and `ParamikoRemoteRunner.run_python()` sends it through shell stdin/heredoc-style execution in `backend/paramiko_remote.py:545`.

Impact: current parser/context sizes are small and bounded, so this is not the large-KPOINTS failure. But if the parser grows substantially, this path could become another command-size or quoting concern.

Direction: consider uploading the parser to a temporary remote file and executing it by path if source size grows or if shell quoting issues appear.

## 9. Results / Remote Parser

### Finding RP-1: The remote parser has a narrow JSON-safe context boundary

Severity: INFO  
Evidence: VERIFIED

`remote_result_parser_context()` in `backend/results.py:635` only passes JSON-native scheduler fields and calculation/workflow specs. Unsupported Python objects raise `TypeError` through `_json_safe_scalar()` in `backend/results.py:691`, rather than being stringified.

Impact: the previous `RemoteJobStatus is not JSON serializable` failure is fixed in a clean architectural direction.

Direction: keep this boundary narrow. Do not use `default=str` for remote parser payloads.

### Finding RP-2: Band/DOS renderer should remain theory-agnostic

Severity: INFO  
Evidence: VERIFIED

The remote parser decides workflow visualizations from workflow/stage types in `backend/remote_result_parser.py:170`, not from PBE/HSE-specific result branches. Band parsing calls pymatgen's `get_band_structure()` with the KPOINTS filename in `backend/remote_result_parser.py:411`, and presentation defaults include the `[-10, 10]` y-axis viewport in `backend/remote_result_parser.py:386`.

Impact: good separation. The renderer should continue to work for PBE or HSE06 as long as the resulting pymatgen objects are valid.

Direction: preserve this architecture for future Elastic/Dielectric visualizations.

### Finding RP-3: Result parser version is not tied to the submitted job

Severity: MEDIUM  
Evidence: VERIFIED / INFERRED

The job submission packages selected backend modules in `backend/submission.py`, but results parsing later sends the current web-process copy of `backend/remote_result_parser.py` from `backend/results.py:576`. That means a job submitted under one parser version may be loaded under a later parser version.

Impact: this is often desirable for parser bug fixes, but it weakens strict reproducibility and can change visualization/result metadata after the fact.

Direction: record parser version/source hash with result payloads. Decide whether Load Results should use "current parser" intentionally or optionally use a parser snapshot from submission time.

## 10. Security

### Finding SE-1: Authentication and CSRF are the main security blockers

Severity: HIGH  
Evidence: VERIFIED

See MU-1. This is the highest security concern because BMD Compute controls a shared cluster account and remote filesystem operations.

Direction: do not expose the app publicly without an upstream auth boundary and form/request protection.

### Finding SE-2: Tracebacks and backend exception internals can be displayed in the UI

Severity: MEDIUM  
Evidence: VERIFIED

Monitoring failure diagnostics render `monitoring_result.exception_debug` fields including traceback in `templates/index.html:1909`. Results failures render traceback text in `templates/index.html:2222`. The monitoring code attaches exception debug data in `backend/monitoring.py:259`.

Impact: useful during development, but production users can see internal module names, class names, paths, and tracebacks. Combined with no auth, this is a meaningful information leak.

Direction: gate detailed tracebacks behind a debug flag or developer-only role. Keep student-facing messages concise and actionable.

### Finding SE-3: Unknown SSH host keys are accepted

Severity: MEDIUM/HIGH  
Evidence: VERIFIED

See RE-4.

Direction: strict known-hosts verification for production.

### Finding SE-4: External script dependency is loaded from a CDN

Severity: LOW/MEDIUM  
Evidence: VERIFIED

The template loads 3Dmol from `https://3Dmol.org/build/3Dmol-min.js` in `templates/index.html:2278`.

Impact: reasonable for development, but production should consider pinning, subresource integrity, or vendoring to reduce supply-chain and availability risk.

Direction: document or harden the asset-loading policy before public deployment.

## 11. Reproducibility

### Finding RPB-1: The submitted backend code snapshot is better than before, but provenance is incomplete

Severity: MEDIUM  
Evidence: VERIFIED

`remote_preparation_file_groups()` packages backend/runtime files for the remote run in `backend/submission.py:497`, which is good. The runtime prints package versions through `runtime_environment_summary()` in `backend/execution.py:19`, but the structured submission spec does not appear to store the Git commit, backend source hash, atomate2/pymatgen/custodian versions, or POTCAR hashes.

Impact: a completed remote run is reproducible in practice if the generated inputs and uploaded backend files are archived, but not fully auditable from a structured `submission.json` alone.

Direction: add a provenance block to `submission.json`: Git commit or dirty tree marker, backend module hashes, selected Python package versions, VASP command, module list, POTCAR functional/symbols and ideally POTCAR hashes if available.

### Finding RPB-2: Dependencies are not pinned and pytest is absent from the environment file

Severity: MEDIUM  
Evidence: VERIFIED

`environment.yml:1` defines the environment with broad dependency names and no version pins for major scientific libraries. It also does not list pytest, although tests rely on it. `pytest.ini:2` now disables pytest cache provider, which solved the local Windows cache ACL problem.

Impact: fresh installs can drift from the validated atomate2/pymatgen/custodian behavior. This is especially important because HSEBS and custodian `auto_nbands` behavior are version-sensitive.

Direction: add a lock file or at least document tested versions. Include pytest in developer/test dependencies.

### Finding RPB-3: POTCAR provenance is only partially recorded

Severity: MEDIUM  
Evidence: VERIFIED / INFERRED

`create_submission_spec()` records POTCAR symbols and functional/source information in `backend/submission.py:1022`, but not hashes of the actual POTCAR data.

Impact: generated inputs can be reproduced only if the remote POTCAR library remains unchanged and equivalent.

Direction: record POTCAR hashes or verified library version metadata if the local policy permits.

## 12. Deployment

### Finding DEP-1: Deployment documentation is not current enough for student operations

Severity: MEDIUM  
Evidence: VERIFIED

`README.md:13` still says "Early development." `ROADMAP.md:117` and `ROADMAP.md:133` still show result visualizations and HSE06 unchecked. `docs/architecture.md` and `docs/calculation_architecture.md` describe several remote/HSE areas as future or partial even though they are implemented.

Impact: new developers or operators will not know the current supported workflows, validated environment, or production precautions.

Direction: update README and docs after this review with: setup, exact conda environment, how to run tests, how to start Uvicorn, supported workflows/modifiers, deployment assumptions, auth status, and known limitations.

### Finding DEP-2: Production process model is still undefined

Severity: MEDIUM  
Evidence: VERIFIED / INFERRED

The code is a FastAPI app, but the repository does not show deployment config for systemd, reverse proxy, HTTPS, workers, log retention, secrets, health checks, or queue/backpressure. ROADMAP still has production deployment unchecked in `ROADMAP.md:145`.

Impact: local validation can pass while production behavior under student use remains fragile.

Direction: write an ops runbook before wider student use. Include single-worker vs multi-worker implications for in-memory cache, remote operation limits, logs, and restart behavior.

## 13. Documentation / Onboarding

### Finding DOC-1: ROADMAP and architecture docs lag the implementation

Severity: MEDIUM  
Evidence: VERIFIED

Examples:

- `ROADMAP.md:117` still marks additional result visualizations unchecked.
- `ROADMAP.md:133` still marks HSE06 unchecked.
- `docs/atomate2_capabilities.md:95` describes HSE06 as manually supplied legacy settings.
- `docs/atomate2_capabilities.md:252` lists HSEBS makers as recommended future candidates even though current code uses them.

Impact: developers may re-solve old problems or misjudge what is production-supported.

Direction: convert the docs from aspirational snapshots into current policy documentation. Keep ROADMAP as future work, but add a "Current supported state" table.

### Finding DOC-2: The reference notebook remains valuable but has diverged

Severity: LOW  
Evidence: VERIFIED / POLICY

The reference notebook still contains validated historical policy such as `VASP_CMD="mpirun -n $SLURM_NTASKS vasp_std"`, static/relax defaults, and notebook-specific NCORE values. Current BMD Compute has intentionally diverged in some places, such as resource-derived NCORE=8 and stage-first HSE/SOC policies.

Impact: this is fine if intentional, but drift should be documented. Otherwise the notebook can become an ambiguous source of truth.

Direction: add a migration/status note beside notebook-derived policies: "preserved", "changed after PowerSLURM validation", or "not yet migrated".

## 14. Maintainability / Tests

### Finding MT-1: Test coverage is now broad and valuable

Severity: INFO  
Evidence: VERIFIED

The test suite covers many of the high-risk areas that were historically fragile: HSE06 theory policy, HSE06 relax/static, HSE06 band structure, DOS and band chaining, generated inputs, remote lifecycle, results parser context, monitor latency, memory dropdown, submission, and VASP command expansion.

Impact: strong safety net for the current stage-first design.

Direction: preserve the focused regression-test style. Add tests for new workflow policies before enabling them in the UI.

### Finding MT-2: Several tests execute assertions at import time

Severity: LOW/MEDIUM  
Evidence: VERIFIED

Files such as `tests/test_calculations.py` and `tests/test_dos_stage_chaining.py` contain substantial top-level assertions rather than only pytest test functions.

Impact: pytest still runs the suite successfully, but import-time assertions make fixtures, parametrization, selective execution, and failure isolation harder.

Direction: gradually convert top-level assertions to named pytest functions as files are touched. Do not block feature work on a mass rewrite.

### Finding MT-3: `pytest.ini` is the right fix for the Windows cache ACL issue

Severity: INFO  
Evidence: VERIFIED

`pytest.ini:2` sets `addopts = -p no:cacheprovider`, avoiding `.pytest_cache` creation/use by default.

Impact: this keeps local Codex/Windows ACL quirks out of the application and test logic.

Direction: keep this minimal config unless broader pytest configuration becomes necessary.

## 15. Previous Review Findings - Current Status

| Previous finding / concern | Current status | Evidence | Notes |
| --- | --- | --- | --- |
| Windows/OpenSSH aliases were not resolved before Paramiko connect. | FIXED | `backend/paramiko_remote.py:152`, `backend/paramiko_remote.py:300` | Uses `ssh -G` plus config fallback and diagnostics. |
| SSH errors only surfaced as generic UI failures with no useful diagnostics. | PARTIALLY FIXED | `backend/paramiko_remote.py:121`, `backend/remote_preparation.py:237` | Logging diagnostics exist; UI still intentionally generic. |
| Temporary SSH debug prints polluted production code. | FIXED | No unconditional investigation prints found in reviewed remote paths. | Normal logging remains. |
| SSH connections/SFTP clients could leak after UI operations. | FIXED | `backend/remote_runtime.py:44`, `tests/test_remote_lifecycle.py` | Cleanup is deterministic and tested. |
| Multi-user SSH saturation could still happen. | STILL VALID | No concurrency cap around remote operations. | Now a legitimate concurrency issue, not primarily a leak. |
| Generated files were embedded into shell commands, causing large KPOINTS failure. | FIXED for active path | `backend/submission.py:497`, `backend/paramiko_remote.py:621` | Legacy heredoc helper remains as cleanup debt. |
| BMD/atomate2/custodian lost the MPI launcher and ran `vasp_std` serially. | FIXED | `backend/execution.py:58`, `tests/test_execution.py:51` | Runtime expands `$SLURM_NTASKS`. |
| HSE06 static inherited PBE tetrahedron smearing. | FIXED | `backend/calculations/theory_policy.py:59`, `tests/test_hse06_theory.py:263` | HSE static now `ISMEAR=0`, `PRECFOCK=Accurate`. |
| HSE06 stages lost resource-derived NCORE. | FIXED | `backend/calculations/resources.py:14`, `tests/test_hse06_theory.py:267` | NCORE remains resource policy, not theory policy. |
| Band Structure automatic NCORE was scientifically unvalidated. | INTENTIONAL POLICY / FIXED | `backend/calculations/resources.py:14`, `tests/test_band_structure_stage_chaining.py:155` | Band stages omit automatic NCORE; explicit override can still work. |
| HSE06 Band Structure was blocked because unreviewed. | FIXED with policy | `backend/workflows.py:1234`, `backend/workflows.py:1561` | Enabled only in compatible stage-first sequences. |
| HSEBS auto-NBANDS caused custodian fatal failure. | FIXED with narrow policy | `backend/calculations/custodian_policy.py:6` | Does not disable handler globally. |
| DOS workflow lacked scientific visualization. | FIXED | `backend/remote_result_parser.py:250`, `templates/index.html:2097` | Uses pymatgen/Plotly payloads. |
| Band Structure visualization details were immature. | FIXED | `backend/remote_result_parser.py:318`, `backend/remote_result_parser.py:545` | Labels, spin legend, y-window, PNG export covered. |
| SOC LELF caused `elf_ncl` custodian failure. | FIXED | `backend/workflows.py:315`, `tests/test_generated_inputs.py:160` | SOC/non-collinear stages omit `LELF`. |
| PBE Static -> SOC WAVECAR reuse was enabled but not valid. | FIXED / POLICY | `backend/workflows.py:261` | WAVECAR carry-forward currently disabled. |
| SOC preview/runtime MAGMOM mismatch. | FIXED | `tests/test_generated_inputs.py:200`, `tests/test_generated_inputs.py:346` | Runtime reconstructed path matches preview MAGMOM. |
| Remote parser context leaked `RemoteJobStatus`. | FIXED | `backend/results.py:635`, `tests/test_results.py:784` | Explicit JSON-safe contract. |
| Resume completed job was slow because results parsed immediately. | FIXED for Resume | `main.py:549`, `templates/index.html:1831` | Load Results is explicit on resume. |
| Refresh Queue Status latency could still include result parsing. | STILL VALID | `main.py:948` | Completed refresh still loads results synchronously. |
| Account was user-editable. | FIXED | `main.py:430`, `templates/index.html:1084` | Backend default account remains. |
| Memory was a free-form field. | FIXED | `backend/calculations/resources.py:11`, `templates/index.html:1101` | Backend-defined dropdown. |
| Dark developer-style UI did not match BMD Lab. | FIXED/PARTIALLY FIXED | `templates/index.html:10` | Light theme tokens and Plotly restyling exist; visual QA should continue. |
| README/docs were stale. | STILL VALID | `README.md:13`, `ROADMAP.md:133` | Documentation now lags more because implementation moved quickly. |
| No production auth/multi-user model. | STILL VALID | `ROADMAP.md:142`, no auth in `main.py` | Main blocker before public/larger student use. |

## 16. Prioritized Recommendations

### Fix immediately

| Item | Why | Suggested direction |
| --- | --- | --- |
| Make `/monitor` never parse completed results automatically. | Prevents queue refresh from blocking on a 900-second remote parse. | Mirror Resume: refresh monitoring state, show Load Results button, parse only on explicit action. |
| Add a completed-job monitor latency regression test. | Current tests cover pending/running and resume explicit load, but not completed refresh behavior. | Monkeypatch `load_results_for_completed_job` to fail during `/monitor` completed refresh unless load requested. |

### Before wider student use

| Item | Why | Suggested direction |
| --- | --- | --- |
| Define authentication, authorization, and job ownership. | Shared cluster account and resume-by-job-ID are not safe as public unauthenticated features. | Use TAU SSO/reverse proxy or app sessions; record owner for every job. |
| Add CSRF protection or equivalent deployment guarantee. | Forms can perform cluster-affecting POST actions. | Add CSRF tokens unless protected by a same-site authenticated gateway with explicit controls. |
| Add bounded remote-operation concurrency. | Cleanup is fixed, but bursts can still create too many legitimate SSH sessions. | Use an application-level semaphore/queue around SSH operations; return a clear busy/backpressure response. |
| Add server-side submission idempotency. | Duplicate POSTs can submit duplicate jobs. | Store a job record keyed by submission fingerprint/idempotency token. |
| Hide detailed tracebacks from normal users. | Internal paths/classes/tracebacks leak in UI. | Gate with debug/developer mode. |

### Near-term

| Item | Why | Suggested direction |
| --- | --- | --- |
| Enforce strict SSH host-key verification in production. | `AutoAddPolicy` trusts unknown hosts. | Load known_hosts from OpenSSH config and fail clearly if missing. |
| Update README, ROADMAP, and architecture docs. | Docs still describe earlier implementation states. | Add current supported workflow table and deployment checklist. |
| Record structured provenance in `submission.json`. | Reproducibility depends on environment/source details not currently structured. | Add git/source hashes, package versions, POTCAR hashes if available. |
| Clarify generated input previews for downstream stages. | Stage 2 POSCAR cannot be the actual post-relaxation structure before Stage 1 runs. | Small UI/doc label; no behavior change. |

### Long-term

| Item | Why | Suggested direction |
| --- | --- | --- |
| Introduce durable job/result records. | Hidden form state and remote JSON are not enough for multi-user production. | Database-backed `JobRecord`/`ResultSummary`. |
| Consider background result parsing. | Explicit Load Results can still occupy a request thread. | Queue parse jobs and poll compact status/result payloads. |
| Generalize stage directory execution for dynamic jobflow detours/additions. | Future atomate2 workflows may not be simple linear chains. | Add tests and directory policy before enabling such workflows. |
| Gradually convert import-time assertion tests to pytest functions. | Improves isolation and maintainability. | Opportunistic cleanup when touching files. |

## 17. Open Questions Requiring Lee's Decision

1. What is the intended production access model: public BMD Lab page behind TAU auth, VPN-only, lab-only, or per-user application login?
2. Should users be allowed to resume/load results for any known SLURM job ID, or only for jobs associated with their authenticated identity/session?
3. Should Refresh Queue Status ever auto-load results, or should Load Results be the only result-parsing action everywhere?
4. What maximum number of simultaneous SSH-backed operations is acceptable for the PowerSLURM login node and BMD Compute process?
5. Should production SSH fail on unknown host keys, and where should the trusted known-hosts file live?
6. Should completed results always be parsed with the current BMD parser, or should each job archive the parser version used at submission time?
7. Which SOC workflows are scientifically next for validation: PBE static + SOC only, DFT+U + SOC, HSE06 + SOC, or SOC relaxations?
8. What provenance level is required for published/student calculations: generated VASP inputs only, submission JSON, package versions, git commit, POTCAR hashes, or full archived backend snapshot?

## Validation

Validation was run with the intended conda environment using `conda run -n bmd-compute`.

```text
python -m pytest tests -q
  64 passed, 15 warnings in 9.49s

python -m compileall backend tests
  passed

git diff --check
  passed

git status --short
  ?? CODE_REVIEW_CURRENT.md
```

Note: invoking the environment's bare `python.exe` directly from this PowerShell session produced a native Windows NumPy `linalg.inv` crash because the conda DLL search path was not activated. The same suite passed through `conda run`, so this is classified as an environment activation issue rather than a repository failure.
