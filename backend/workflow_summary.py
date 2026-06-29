def summarize_workflow(flow, workflow_type):
    """
    Return a template-friendly summary of a Jobflow Flow.
    """

    workflow_labels = {
        "static": "Static",
        "relax": "Relaxation",
        "relax_ions": "Relax Ions",
    }
    jobs = list(getattr(flow, "jobs", []) or [])

    return {
        "workflow_type": workflow_labels.get(workflow_type, workflow_type),
        "flow_name": getattr(flow, "name", "Unknown"),
        "number_of_jobs": len(jobs),
        "job_names": [getattr(job, "name", "Unknown") for job in jobs],
        "ready_for_submission": True,
    }
