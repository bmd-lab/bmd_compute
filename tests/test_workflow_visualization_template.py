from __future__ import annotations

from pathlib import Path


TEMPLATE = Path("templates/index.html")


source = TEMPLATE.read_text(encoding="utf-8")

for token in (
    "--bmd-page-bg",
    "--bmd-surface",
    "--bmd-surface-secondary",
    "--bmd-text",
    "--bmd-text-muted",
    "--bmd-border",
    "--bmd-accent",
    "--bmd-accent-hover",
    "--bmd-success",
    "--bmd-warning",
    "--bmd-danger",
):
    assert token in source

for retired_dark_color in (
    "#111111",
    "#202020",
    "#1b1b1b",
    "#121212",
    "#3a3a3a",
    "#555555",
    "#f2f2f2",
    "#4FC3F7",
    "#B2EBF2",
    "#FF8A80",
    "#7ED957",
):
    assert retired_dark_color not in source

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
assert "plotImageWithExportTypography(target)" in source
assert "clonePlotlyObject(target.data || [])" in source
assert "exportLayoutWithReadableTypography(target.layout || {})" in source
assert "window.Plotly.newPlot(exportTarget, data, layout, config)" in source
assert "window.Plotly.toImage(exportTarget, EXPORT_IMAGE_OPTIONS)" in source
assert "window.Plotly.purge(exportTarget)" in source
assert "window.Plotly.relayout" not in source
assert 'bmdThemeColor("--bmd-surface", "#ffffff")' in source
assert 'bmdThemeColor("--bmd-text", "#000000")' in source
assert 'bmdThemeColor("--bmd-border", "#cbdce9")' in source
assert 'bmdThemeColor("--bmd-accent", "#1a126e")' in source
assert 'bmdThemeColor("--bmd-plot-grid", "#e3eaf0")' in source
assert "paper_bgcolor: plotTheme.surface" in source
assert "plot_bgcolor: plotTheme.surface" in source
assert "color: plotTheme.text" in source
assert "gridcolor: plotTheme.grid" in source
assert "zerolinecolor: plotTheme.borderStrong" in source
assert "linecolor: plotTheme.borderStrong" in source
assert 'format: "png"' in source
assert "width: 1600" in source
assert "height: 1000" in source
assert "scale: 2" in source
assert "axisTitle: 28" in source
assert "tick: 22" in source
assert "legend: 22" in source
assert "var EXPORT_MARGINS = {" in source
assert "l: 110" in source
assert "r: 44" in source
assert "t: 52" in source
assert "b: 110" in source
assert "exportLayout.margin = Object.assign({}, exportLayout.margin || {}, EXPORT_MARGINS)" in source
assert "applyAxisTitleFont(exportLayout.xaxis, EXPORT_TYPOGRAPHY.axisTitle)" in source
assert "applyAxisTitleFont(exportLayout.yaxis, EXPORT_TYPOGRAPHY.axisTitle)" in source
assert "exportLayout.xaxis.tickfont.size = EXPORT_TYPOGRAPHY.tick" in source
assert "exportLayout.yaxis.tickfont.size = EXPORT_TYPOGRAPHY.tick" in source
assert "exportLayout.legend.font.size = EXPORT_TYPOGRAPHY.legend" in source
assert "var yaxisRange = (" in source
assert "Array.isArray(plot.yaxis_range)" in source
assert "range: yaxisRange" in source
assert "link.download = filename" in source

assert source.count("plot-download-button") == 2

print("workflow visualization template smoke test passed")
