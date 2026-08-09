from __future__ import annotations

from pathlib import Path


TEMPLATE = Path("templates/index.html")


source = TEMPLATE.read_text(encoding="utf-8")

assert "Download PNG" in source
assert source.count('data-download-plot="{{ visualization.element_id }}"') == 1
assert source.count(
    'data-download-filename="{{ visualization.download_filename or \'\' }}"'
) == 1

line_plot_block_start = source.index('{% if visualization.kind == "line_plot" %}')
line_plot_block_end = source.index("{% endif %}", line_plot_block_start)
download_button_index = source.index('data-download-plot="{{ visualization.element_id }}"')
assert line_plot_block_start < download_button_index < line_plot_block_end

assert 'var target = document.getElementById(visualization.element_id);' in source
assert 'findPlotDownloadButton(visualization.element_id)' in source
assert 'button.getAttribute("data-download-filename")' in source
assert "window.Plotly.toImage(target, {" in source
assert 'format: "png"' in source
assert "width: 1600" in source
assert "height: 1000" in source
assert "scale: 2" in source
assert "link.download = filename" in source

assert source.count("plot-download-button") == 2

print("workflow visualization template smoke test passed")
