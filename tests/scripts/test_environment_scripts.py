from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _copy_scripts(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "scripts/lib").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/install-wsl.sh", repository / "scripts/install-wsl.sh")
    shutil.copy2(
        ROOT / "scripts/migrate-environment.sh",
        repository / "scripts/migrate-environment.sh",
    )
    shutil.copy2(
        ROOT / "scripts/lib/operamind-env.sh",
        repository / "scripts/lib/operamind-env.sh",
    )
    return repository


def _valid_wsl_environment(*, bridge_token: str = "a" * 64, web_token: str = "b" * 64) -> str:
    return "\n".join(
        (
            "OPERAMIND_DATABASE_URL=postgresql://operamind:test@127.0.0.1:5432/operamind",
            "OPERAMIND_TEST_DATABASE_URL=postgresql://operamind:test@127.0.0.1:5432/operamind_test",
            f"OPERAMIND_BRIDGE_TOKEN={bridge_token}",
            f"OPERAMIND_WEB_TOKEN={web_token}",
            "OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1",
            "OPERAMIND_POSTGRES_CONTAINER=operamind-postgres",
            "OPERAMIND_POSTGRES_PORT=5432",
            "OPERAMIND_POSTGRES_PASSWORD=test",
            "",
        )
    )


def _run(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_install_wsl_parses_valid_environment_without_shell_evaluation(tmp_path: Path) -> None:
    repository = _copy_scripts(tmp_path)
    marker = tmp_path / "must-not-exist"
    malicious = _valid_wsl_environment(
        bridge_token=f"$(touch${{IFS}}{marker})",
    )
    (repository / ".env.wsl").write_text(malicious, encoding="utf-8")

    result = _run(
        str(repository / "scripts/install-wsl.sh"),
        "status",
        "--dry-run",
        cwd=repository,
        env=os.environ.copy(),
    )

    assert result.returncode != 0
    assert "BRIDGE_TOKEN must be" in result.stderr
    assert not marker.exists()

    (repository / ".env.wsl").write_text(_valid_wsl_environment(), encoding="utf-8")
    valid = _run(
        str(repository / "scripts/install-wsl.sh"),
        "status",
        "--dry-run",
        cwd=repository,
        env=os.environ.copy(),
    )
    assert valid.returncode == 0, valid.stderr
    assert "podman ps" in valid.stdout

    (repository / ".env.wsl").write_text(_valid_wsl_environment(web_token="weak"), encoding="utf-8")
    weak_web_token = _run(
        str(repository / "scripts/install-wsl.sh"),
        "status",
        "--dry-run",
        cwd=repository,
        env=os.environ.copy(),
    )
    assert weak_web_token.returncode != 0
    assert "WEB_TOKEN must be" in weak_web_token.stderr


def test_install_wsl_rejects_unknown_and_duplicate_environment_keys(tmp_path: Path) -> None:
    repository = _copy_scripts(tmp_path)
    env_file = repository / ".env.wsl"
    env_file.write_text(_valid_wsl_environment() + "UNEXPECTED_KEY=value\n", encoding="utf-8")

    unknown = _run(
        str(repository / "scripts/install-wsl.sh"),
        "status",
        "--dry-run",
        cwd=repository,
        env=os.environ.copy(),
    )

    assert unknown.returncode != 0
    assert "Unsupported environment key" in unknown.stderr

    env_file.write_text(
        _valid_wsl_environment() + "OPERAMIND_POSTGRES_PORT=5433\n",
        encoding="utf-8",
    )
    duplicate = _run(
        str(repository / "scripts/install-wsl.sh"),
        "status",
        "--dry-run",
        cwd=repository,
        env=os.environ.copy(),
    )
    assert duplicate.returncode != 0
    assert "Duplicate environment key" in duplicate.stderr


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_tool_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    restore_capture = tmp_path / "restore.dump"
    migrate_marker = tmp_path / "migrated"
    _write_executable(
        binaries / "pg_dump",
        """#!/usr/bin/env bash
set -eu
output=''
for arg in "$@"; do
  case "$arg" in --file=*) output="${arg#--file=}" ;; esac
done
if [[ -n "$output" ]]; then printf 'FAKE-DUMP' >"$output"; else printf 'FAKE-DUMP'; fi
""",
    )
    _write_executable(
        binaries / "psql",
        """#!/usr/bin/env bash
case "$*" in
  *"FROM pg_tables"*) printf 'public.items\\n' ;;
  *"SELECT count(*) FROM public.items"*) printf '2\\n' ;;
  *) exit 1 ;;
esac
""",
    )
    _write_executable(
        binaries / "podman",
        """#!/usr/bin/env bash
set -eu
case "${1-}" in
  info) exit 0 ;;
  container) [[ "${2-}" == exists ]] && exit 0 ;;
  inspect) printf 'true\\n'; exit 0 ;;
  start) exit 0 ;;
  exec)
    shift
    if [[ "${1-}" == -i ]]; then shift; interactive=1; else interactive=0; fi
    shift
    command="${1-}"
    shift || true
    case "$command" in
      psql)
        case "$*" in
          *"FROM pg_tables"*) printf 'public.items\\n' ;;
          *"SELECT count(*) FROM public.items"*) printf '2\\n' ;;
          *) exit 1 ;;
        esac
        ;;
      pg_dump) printf 'PREVIOUS-DUMP' ;;
      pg_restore) cat >"$FAKE_RESTORE_CAPTURE" ;;
      dropdb | createdb) exit 0 ;;
      *) exit 1 ;;
    esac
    ;;
  *) exit 1 ;;
esac
""",
    )
    environment = os.environ.copy()
    environment.pop("OPERAMIND_DATABASE_URL", None)
    environment["PATH"] = f"{binaries}{os.pathsep}{environment['PATH']}"
    environment["FAKE_RESTORE_CAPTURE"] = str(restore_capture)
    environment["FAKE_MIGRATE_MARKER"] = str(migrate_marker)
    return environment, migrate_marker


def test_environment_bundle_export_verify_and_restore_closure(tmp_path: Path) -> None:
    repository = _copy_scripts(tmp_path)
    environment, migrate_marker = _fake_tool_environment(tmp_path)
    evidence = repository / "readiness/evidence"
    evidence.mkdir(parents=True)
    (evidence / "source-evidence.json").write_text('{"status":"passed"}\n', encoding="utf-8")
    bundle = tmp_path / "operamind-migration.tar.gz"
    script = str(repository / "scripts/migrate-environment.sh")

    exported = _run(
        script,
        "export",
        "--output",
        str(bundle),
        "--database-url",
        "postgresql://operamind:test@127.0.0.1:5432/operamind",
        cwd=repository,
        env=environment,
    )
    assert exported.returncode == 0, exported.stderr
    assert bundle.is_file()
    assert Path(f"{bundle}.sha256").is_file()

    verified = _run(script, "verify", "--bundle", str(bundle), cwd=repository, env=environment)
    assert verified.returncode == 0, verified.stderr

    (repository / ".env.wsl").write_text(_valid_wsl_environment(), encoding="utf-8")
    (repository / ".venv/bin").mkdir(parents=True)
    _write_executable(
        repository / ".venv/bin/operamind-migrate",
        "#!/usr/bin/env bash\nset -eu\n"
        ': "${OPERAMIND_DATABASE_URL:?}"\n'
        'touch "$FAKE_MIGRATE_MARKER"\n',
    )
    (evidence / "source-evidence.json").unlink()

    without_replace = _run(
        script,
        "restore",
        "--bundle",
        str(bundle),
        cwd=repository,
        env=environment,
    )
    assert without_replace.returncode != 0
    assert "requires --replace" in without_replace.stderr

    restored = _run(
        script,
        "restore",
        "--bundle",
        str(bundle),
        "--replace",
        cwd=repository,
        env=environment,
    )
    assert restored.returncode == 0, restored.stderr
    assert (evidence / "source-evidence.json").is_file()
    assert migrate_marker.is_file()
    assert (tmp_path / "restore.dump").read_bytes() == b"FAKE-DUMP"
    assert list((repository / ".operamind-backups").glob("pre-restore-*.dump"))
    receipts = list(evidence.glob("environment-restore-*.json"))
    assert len(receipts) == 1
    assert '"status": "passed"' in receipts[0].read_text(encoding="utf-8")


def test_container_export_wins_over_inherited_database_url(tmp_path: Path) -> None:
    repository = _copy_scripts(tmp_path)
    environment, _ = _fake_tool_environment(tmp_path)
    environment["OPERAMIND_DATABASE_URL"] = (
        "postgresql://operamind:inherited@127.0.0.1:5432/operamind"
    )
    bundle = tmp_path / "container-migration.tar.gz"

    result = _run(
        str(repository / "scripts/migrate-environment.sh"),
        "export",
        "--output",
        str(bundle),
        "--source-container",
        "operamind-postgres",
        cwd=repository,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert bundle.is_file()


def test_environment_bundle_detects_transport_tampering(tmp_path: Path) -> None:
    repository = _copy_scripts(tmp_path)
    environment, _ = _fake_tool_environment(tmp_path)
    bundle = tmp_path / "operamind-migration.tar.gz"
    script = str(repository / "scripts/migrate-environment.sh")
    exported = _run(
        script,
        "export",
        "--output",
        str(bundle),
        "--database-url",
        "postgresql://operamind:test@127.0.0.1:5432/operamind",
        cwd=repository,
        env=environment,
    )
    assert exported.returncode == 0, exported.stderr
    with bundle.open("ab") as stream:
        stream.write(b"tampered")

    verified = _run(script, "verify", "--bundle", str(bundle), cwd=repository, env=environment)

    assert verified.returncode != 0
    assert "Bundle checksum mismatch" in verified.stderr
