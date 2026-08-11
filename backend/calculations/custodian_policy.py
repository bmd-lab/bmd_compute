from __future__ import annotations

from copy import deepcopy


HSE_BAND_STRUCTURE_CUSTODIAN_ERROR_EXCLUSIONS = ("auto_nbands",)


def hse_band_structure_run_vasp_kwargs() -> dict:
    return {
        "handlers": custodian_handlers_excluding_errors(
            HSE_BAND_STRUCTURE_CUSTODIAN_ERROR_EXCLUSIONS
        )
    }


def custodian_handlers_excluding_errors(excluded_errors) -> tuple:
    """
    Return atomate2's default VASP Custodian handlers with selected
    VaspErrorHandler messages removed.

    Newer Custodian releases already treat ``auto_nbands`` as a warning, but
    older deployed environments can still terminate otherwise healthy VASP
    band-structure jobs. This keeps the override local to the maker that opts
    into it rather than mutating global atomate2 defaults.
    """

    from atomate2.vasp.run import DEFAULT_HANDLERS
    from custodian.vasp.handlers import VaspErrorHandler

    excluded = set(excluded_errors or ())
    handlers = []
    for handler in DEFAULT_HANDLERS:
        if isinstance(handler, VaspErrorHandler):
            handlers.append(
                _vasp_error_handler_without_errors(
                    handler,
                    VaspErrorHandler,
                    excluded,
                )
            )
        else:
            handlers.append(deepcopy(handler))

    return tuple(handlers)


def _vasp_error_handler_without_errors(handler, handler_cls, excluded_errors: set[str]):
    available_errors = list(getattr(handler_cls, "error_msgs", {}))
    current_subset = list(getattr(handler, "errors_subset_to_catch", available_errors))
    errors_subset = [
        error
        for error in current_subset
        if error not in excluded_errors
    ]
    output_filename = getattr(handler, "output_filename", "vasp.out")
    vtst_fixes = getattr(handler, "vtst_fixes", False)

    try:
        return handler_cls(
            output_filename=output_filename,
            errors_subset_to_catch=errors_subset,
            vtst_fixes=vtst_fixes,
        )
    except TypeError:
        return handler_cls(
            output_filename=output_filename,
            errors_subset_to_catch=errors_subset,
        )


__all__ = [
    "HSE_BAND_STRUCTURE_CUSTODIAN_ERROR_EXCLUSIONS",
    "custodian_handlers_excluding_errors",
    "hse_band_structure_run_vasp_kwargs",
]
