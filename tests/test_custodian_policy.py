import json
import os

from atomate2.vasp.run import DEFAULT_HANDLERS, _DEFAULT_VALIDATORS
from custodian.vasp.handlers import VaspErrorHandler

from backend.calculations.custodian_policy import (
    BMD_CUSTODIAN_POLICY_RATIONALE,
    FROZEN_JOB_HANDLER,
    bmd_custodian_handlers,
    resolved_custodian_policy,
)
from backend.calculations.models import Modifier, StageSpec, StageType, Theory
from backend.calculations.registry import validate_stage_spec
from backend.workflows import run_vasp_kwargs_for_modifiers


RETAINED_HANDLER_NAMES = [
    "VaspErrorHandler",
    "MeshSymmetryErrorHandler",
    "UnconvergedErrorHandler",
    "NonConvergingErrorHandler",
    "PotimErrorHandler",
    "PositiveEnergyErrorHandler",
    "StdErrHandler",
    "LargeSigmaHandler",
    "IncorrectSmearingHandler",
    "KspacingMetalHandler",
]


def _handler_names(handlers):
    return [type(handler).__name__ for handler in handlers]


def _vasp_error_handler(handlers):
    return next(handler for handler in handlers if isinstance(handler, VaspErrorHandler))


def test_bmd_policy_removes_only_frozen_handler_from_atomate2_defaults():
    handlers = bmd_custodian_handlers()
    expected = [
        handler
        for handler in DEFAULT_HANDLERS
        if type(handler).__name__ != "FrozenJobErrorHandler"
    ]

    assert _handler_names(handlers) == RETAINED_HANDLER_NAMES
    assert _handler_names(handlers) == _handler_names(expected)
    assert [handler.as_dict() for handler in handlers] == [
        handler.as_dict() for handler in expected
    ]
    assert "FrozenJobErrorHandler" not in _handler_names(handlers)


def test_handler_calls_receive_independent_state():
    first = bmd_custodian_handlers()
    second = bmd_custodian_handlers()

    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))
    first_vasp = _vasp_error_handler(first)
    second_vasp = _vasp_error_handler(second)
    first_vasp.errors_subset_to_catch.remove("brmix")
    assert "brmix" in second_vasp.errors_subset_to_catch


def test_output_silence_is_not_an_actionable_bmd_handler(tmp_path):
    output = tmp_path / "vasp.out"
    output.write_text("still computing\n", encoding="utf-8")
    os.utime(output, (1, 1))

    handlers = bmd_custodian_handlers()
    assert FROZEN_JOB_HANDLER not in {
        f"{type(handler).__module__}.{type(handler).__name__}"
        for handler in handlers
    }
    assert all("timeout" not in handler.as_dict() for handler in handlers)


def test_hse_terminal_policy_removes_only_auto_nbands_from_vasp_errors():
    base_handlers = bmd_custodian_handlers()
    hse_handlers = bmd_custodian_handlers(excluded_vasp_errors=("auto_nbands",))
    base_errors = set(_vasp_error_handler(base_handlers).errors_subset_to_catch)
    hse_errors = set(_vasp_error_handler(hse_handlers).errors_subset_to_catch)

    assert base_errors - hse_errors == {"auto_nbands"}
    assert hse_errors - base_errors == set()
    assert _handler_names(hse_handlers) == RETAINED_HANDLER_NAMES


def test_resolved_policy_records_runtime_handlers_validators_and_slurm_authority():
    policy = resolved_custodian_policy(StageType.STATIC, Theory.PBE)
    serialized = json.dumps(policy, sort_keys=True, allow_nan=False)

    assert json.loads(serialized) == policy
    assert [item["class"].rsplit(".", 1)[-1] for item in policy["handlers"]] == (
        RETAINED_HANDLER_NAMES
    )
    assert policy["explicit_handler_exclusions"] == [FROZEN_JOB_HANDLER]
    assert policy["walltime_authority"] == "slurm"
    assert policy["walltime_handler"] is None
    assert policy["rationale"] == BMD_CUSTODIAN_POLICY_RATIONALE
    assert [item["class"] for item in policy["validators"]["resolved"]] == [
        f"{type(validator).__module__}.{type(validator).__name__}"
        for validator in _DEFAULT_VALIDATORS
    ]
    assert [item["configuration"] for item in policy["validators"]["resolved"]] == [
        {
            key: value
            for key, value in validator.as_dict().items()
            if not key.startswith("@")
        }
        for validator in _DEFAULT_VALIDATORS
    ]


def test_provenance_handler_descriptions_match_execution_factory():
    policy = resolved_custodian_policy(StageType.STATIC, Theory.PBE)
    handlers = bmd_custodian_handlers()
    expected = []
    for handler in handlers:
        serialized = handler.as_dict()
        expected.append({
            "class": f"{serialized['@module']}.{serialized['@class']}",
            "configuration": {
                key: value
                for key, value in serialized.items()
                if not key.startswith("@")
            },
        })

    assert policy["handlers"] == expected


def test_all_common_run_kwargs_use_policy_without_internal_walltime():
    pbe_kwargs = run_vasp_kwargs_for_modifiers(())
    soc_kwargs = run_vasp_kwargs_for_modifiers({Modifier.SOC})

    for kwargs in (pbe_kwargs, soc_kwargs):
        assert _handler_names(kwargs["handlers"]) == RETAINED_HANDLER_NAMES
        assert "wall_time" not in kwargs
        assert all(type(handler).__name__ != "WalltimeHandler" for handler in kwargs["handlers"])
    assert "vasp_cmd" not in pbe_kwargs
    assert soc_kwargs["vasp_cmd"].endswith("vasp_ncl")


def test_hse06_static_soc_silence_regression_keeps_ncl_without_frozen_handler():
    stage = validate_stage_spec(
        StageSpec(StageType.STATIC, Theory.HSE06, {Modifier.SOC})
    )
    kwargs = run_vasp_kwargs_for_modifiers(stage.modifiers)

    assert stage.theory is Theory.HSE06
    assert kwargs["vasp_cmd"].endswith("vasp_ncl")
    assert "FrozenJobErrorHandler" not in _handler_names(kwargs["handlers"])
    assert all("timeout" not in handler.as_dict() for handler in kwargs["handlers"])
