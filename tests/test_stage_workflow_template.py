from __future__ import annotations

from pathlib import Path


source = Path("templates/index.html").read_text(encoding="utf-8")

assert 'name="workflow_spec_json"' in source
assert 'id="workflow-recipe-select"' in source
assert 'id="workflow-stage-list"' in source
assert 'data-workflow-stage' in source
assert 'data-stage-type' in source
assert 'data-stage-theory' in source
assert 'data-stage-modifier' in source
assert 'id="add-workflow-stage"' in source
assert "Recommended Workflow" in source
assert "Custom Workflow" in source
assert "Calculation Type" in source
assert "Level of Theory" in source
assert "Advanced Options" in source
calculation_definition = source[source.index('<form\n                id="calculation-review-form"'):]
assert calculation_definition.index("<h3>Execution Resources</h3>") < calculation_definition.index("<h3>Scientific Specification</h3>")
resource_panel = calculation_definition[
    calculation_definition.index("<h3>Execution Resources</h3>"):
    calculation_definition.index("<h3>Scientific Specification</h3>")
]
assert resource_panel.index('name="cpus"') < resource_panel.index('name="memory_gb"')
assert resource_panel.index('name="memory_gb"') < resource_panel.index('name="walltime"')
assert resource_panel.index('name="walltime"') < resource_panel.index('name="queue"')
assert "grid-template-columns: minmax(0, 1fr);" in source
assert "grid-template-columns: repeat(4, minmax(140px, 1fr));" in source
assert "workflow_spec_json" in source[source.index('<form action="/prepare-remote"'):]
assert "workflow_spec_json" in source[source.index('<form action="/submit"'):]
assert "workflow_spec_json" in source[source.index('<form action="/monitor"'):]

print("stage workflow template smoke test passed")
