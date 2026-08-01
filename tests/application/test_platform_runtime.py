from operamind.platform_runtime import (
    approved_process_environment,
    subprocess_creation_flags,
    venv_command,
)


def test_windows_process_environment_keeps_loader_and_temp_variables() -> None:
    source = {
        "PATH": "C:\\Windows\\System32",
        "SystemRoot": "C:\\Windows",
        "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        "TEMP": "C:\\Temp",
        "SECRET": "must-not-leak",
    }

    result = approved_process_environment(("PATH",), environ=source, platform_name="nt")

    assert result == {
        "PATH": "C:\\Windows\\System32",
        "SystemRoot": "C:\\Windows",
        "COMSPEC": "C:\\Windows\\System32\\cmd.exe",
        "TEMP": "C:\\Temp",
    }


def test_virtualenv_commands_use_windows_scripts_directory() -> None:
    assert venv_command("pytest", platform_name="nt") == ".venv/Scripts/pytest.exe"
    assert venv_command("python", platform_name="posix") == ".venv/bin/python"


def test_windows_subprocesses_are_hidden_and_get_a_new_process_group() -> None:
    flags = subprocess_creation_flags(platform_name="nt")

    assert flags & 0x08000000
    assert flags & 0x00000200
    assert subprocess_creation_flags(platform_name="posix") == 0
