from __future__ import annotations

from pathlib import Path


TEMPLATE_SOURCE = Path("templates/index.html").read_text(encoding="utf-8")


def _source_between(start_token: str, end_token: str) -> str:
    start = TEMPLATE_SOURCE.index(start_token)
    end = TEMPLATE_SOURCE.index(end_token, start)
    return TEMPLATE_SOURCE[start:end]


def test_copy_buttons_use_modern_clipboard_api_first():
    helper_source = _source_between(
        "function copyTextToClipboard(text)",
        'document.querySelectorAll("[data-copy-input]")',
    )

    assert 'navigator.clipboard && typeof navigator.clipboard.writeText === "function"' in helper_source
    assert "navigator.clipboard.writeText(text)" in helper_source
    assert "return copyTextWithSelectionFallback(text);" in helper_source
    assert helper_source.index("navigator.clipboard.writeText(text)") < helper_source.index(
        "return copyTextWithSelectionFallback(text);"
    )


def test_copy_buttons_fallback_copy_exact_supplied_text():
    fallback_source = _source_between(
        "function copyTextWithSelectionFallback(text)",
        "function copyTextToClipboard(text)",
    )

    assert 'document.createElement("textarea")' in fallback_source
    assert "textarea.value = text;" in fallback_source
    assert 'document.execCommand("copy")' in fallback_source
    assert "textarea.value.trim" not in fallback_source
    assert "textarea.value.replace" not in fallback_source


def test_copy_buttons_remove_temporary_fallback_element():
    fallback_source = _source_between(
        "function copyTextWithSelectionFallback(text)",
        "function copyTextToClipboard(text)",
    )

    assert "document.body.appendChild(textarea);" in fallback_source
    assert "finally {" in fallback_source
    assert "document.body.removeChild(textarea);" in fallback_source
    assert fallback_source.index("document.body.appendChild(textarea);") < fallback_source.index(
        "document.body.removeChild(textarea);"
    )


def test_copy_button_feedback_distinguishes_success_from_failure():
    click_handler_source = _source_between(
        'document.querySelectorAll("[data-copy-input]")',
        "});\n});\n</script>",
    )

    assert "copyTextToClipboard(text).then(function ()" in click_handler_source
    assert 'setButtonState(button, "\\u2713 Copied", "copied");' in click_handler_source
    assert 'setButtonState(button, "Copy failed", "unsupported");' in click_handler_source
    assert "Not supported" not in click_handler_source
    assert click_handler_source.index('setButtonState(button, "\\u2713 Copied", "copied");') < (
        click_handler_source.index('setButtonState(button, "Copy failed", "unsupported");')
    )


def test_generated_input_copy_controls_still_target_rendered_preview_text():
    assert TEMPLATE_SOURCE.count('class="input-copy-button" data-copy-input') == 5
    assert TEMPLATE_SOURCE.count("data-copy-input") == 6
    assert 'var preview = pane ? pane.querySelector(".input-preview") : null;' in TEMPLATE_SOURCE
    assert 'var text = preview ? preview.textContent : "";' in TEMPLATE_SOURCE
    assert "{{ generated_inputs.incar }}" in TEMPLATE_SOURCE
    assert "{{ generated_inputs.kpoints }}" in TEMPLATE_SOURCE
    assert "{{ generated_inputs.poscar }}" in TEMPLATE_SOURCE
    assert "{{ generated_inputs.slurm_script }}" in TEMPLATE_SOURCE
    assert "{{ generated_inputs.exact_slurm_script }}" in TEMPLATE_SOURCE
