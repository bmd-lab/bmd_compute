# BMD Compute Input Reference Contract

BMD Compute exposes a narrow read-only JSON producer for generated VASP input
references:

```bash
python -m backend.calculations.input_reference < request.json
```

The command reads one JSON object from stdin and writes one JSON document to
stdout. It does not submit jobs, call SLURM, open SSH connections, run
Custodian, or run VASP.

The request uses BMD Compute's existing structure and `WorkflowSpec` vocabulary:

```json
{
  "structure": {
    "type": "pasted_text",
    "format": "poscar",
    "text": "..."
  },
  "workflow_spec": {
    "stages": [
      {
        "stage_type": "static",
        "theory": "pbe",
        "modifiers": [],
        "label": null,
        "options": {}
      }
    ],
    "label": null,
    "recipe": null
  },
  "resources": {
    "ntasks": 24,
    "mem_gb": 128
  },
  "potcar_functional": "PBE_64"
}
```

The response contract is versioned with `schema_version: 1` and describes the
`generated_pre_execution` reference phase: the inputs BMD Compute generates
before execution, not later Custodian or VASP corrections. References are
created through the same generated-input path used by the web application and
preserve native JSON values for INCAR settings where practical.

`POTCAR` output is symbolic only. The producer uses pymatgen's `potcar_spec`
mode and exposes `POTCAR.spec` symbols, not licensed POTCAR file contents.

This contract describes BMD Compute's current executable implementation for
read-only comparison by tools such as BMD Agent. It is not a methodology
authority; scientific validation, adoption status, evidence, and provenance
belong outside BMD Compute, currently intended for BMDex.
