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
memory_label_index = resource_panel.index("<label>Memory (GB)</label>")
memory_select_index = resource_panel.index('<select name="memory_gb">')
memory_select_end = resource_panel.index("</select>", memory_select_index)
memory_select_block = resource_panel[memory_select_index:memory_select_end]
assert memory_label_index < memory_select_index
assert "selected_resources.allowed_memory_gb" in memory_select_block
assert 'value="{{ memory_gb }}"' in memory_select_block
assert "{% if selected_resources.memory_gb == memory_gb %}selected{% endif %}" in memory_select_block
assert "{{ memory_gb }} GB" in memory_select_block
queue_label_index = resource_panel.index("<label>Queue</label>")
queue_select_index = resource_panel.index('<select name="queue">')
queue_select_end = resource_panel.index("</select>", queue_select_index)
queue_select_block = resource_panel[queue_select_index:queue_select_end]
assert queue_label_index < queue_select_index
assert "selected_resources.allowed_queues" in queue_select_block
assert 'value="{{ queue }}"' in queue_select_block
assert "{% if selected_resources.queue == queue %}selected{% endif %}" in queue_select_block
assert 'type="number"' not in resource_panel
assert 'step="1"' not in resource_panel
assert "grid-template-columns: minmax(0, 1fr);" in source
assert "grid-template-columns: repeat(4, minmax(140px, 1fr));" in source
assert "workflow_spec_json" in source[source.index('<form action="/prepare-remote"'):]
assert "workflow_spec_json" in source[source.index('<form action="/submit"'):]
assert "workflow_spec_json" in source[source.index('<form action="/monitor"'):]

print("stage workflow template smoke test passed")
