import inspect

from backend.remote import (
    NOTEBOOK_REMOTE_INTERACTIONS,
    RemoteCommandResult,
    RemoteExecutionError,
    RemoteRunner,
)


def test_remote_command_result_ok():
    result = RemoteCommandResult(command="true", returncode=0, stdout="OK")

    assert result.ok
    assert result.raise_for_status() is result


def test_remote_command_result_error():
    result = RemoteCommandResult(command="false", returncode=2, stderr="failed")

    assert not result.ok
    try:
        result.raise_for_status()
    except RemoteExecutionError as exc:
        assert exc.result is result
        assert "failed" in str(exc)
    else:
        raise AssertionError("RemoteExecutionError was not raised")


def test_remote_runner_is_abstraction_only():
    assert inspect.isabstract(RemoteRunner)


def test_notebook_remote_interactions_are_documented():
    expected = {
        "session_lifecycle",
        "tunnels",
        "commands",
        "remote_files",
        "submission",
        "monitoring",
        "results",
    }

    assert expected.issubset(NOTEBOOK_REMOTE_INTERACTIONS)


if __name__ == "__main__":
    test_remote_command_result_ok()
    test_remote_command_result_error()
    test_remote_runner_is_abstraction_only()
    test_notebook_remote_interactions_are_documented()
    print("RemoteRunner smoke test passed.")
