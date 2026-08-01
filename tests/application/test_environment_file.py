from __future__ import annotations

from pathlib import Path

import pytest

from operamind.environment_file import load_environment_file


def test_environment_file_loads_values_without_overriding_process_environment(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        """
        # local database
        OPERAMIND_DATABASE_URL='postgresql:///operamind_vnext?host=/private/tmp&port=5432'
        export EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
        OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1
        """,
        encoding="utf-8",
    )
    environ = {"EMBED_MODEL": "operator-selected-model"}

    applied = load_environment_file(env_file, environ=environ)

    assert applied == ("OPERAMIND_DATABASE_URL", "OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN")
    assert environ == {
        "OPERAMIND_DATABASE_URL": ("postgresql:///operamind_vnext?host=/private/tmp&port=5432"),
        "EMBED_MODEL": "operator-selected-model",
        "OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN": "1",
    }


@pytest.mark.parametrize(
    "content",
    (
        "NOT VALID=value\n",
        "DUPLICATE=one\nDUPLICATE=two\n",
        "UNFINISHED='value\n",
    ),
)
def test_environment_file_rejects_ambiguous_entries(tmp_path: Path, content: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_environment_file(env_file, environ={})


def test_environment_file_accepts_windows_utf8_bom_and_crlf(tmp_path: Path) -> None:
    env_file = tmp_path / "config.env"
    env_file.write_bytes(
        "\ufeffOPERAMIND_DATABASE_URL=postgresql://user:secret@127.0.0.1/db\r\n".encode(
            "utf-8"
        )
    )
    environ: dict[str, str] = {}

    applied = load_environment_file(env_file, environ=environ)

    assert applied == ("OPERAMIND_DATABASE_URL",)
    assert environ["OPERAMIND_DATABASE_URL"].startswith("postgresql://")
