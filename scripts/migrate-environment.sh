#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPOSITORY_ROOT}/.env.wsl"
BACKUP_ROOT="${REPOSITORY_ROOT}/.operamind-backups"

# shellcheck source=scripts/lib/operamind-env.sh
source "${SCRIPT_DIR}/lib/operamind-env.sh"

COMMAND=""
OUTPUT=""
BUNDLE=""
SOURCE_DATABASE_URL=""
DATABASE_URL_EXPLICIT=0
SOURCE_CONTAINER=""
SOURCE_DATABASE="operamind"
EVIDENCE_ROOT="${REPOSITORY_ROOT}/readiness/evidence"
REPLACE=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/migrate-environment.sh export --output BUNDLE [source options]
  ./scripts/migrate-environment.sh verify --bundle BUNDLE
  ./scripts/migrate-environment.sh restore --bundle BUNDLE --replace

Source options:
  --database-url URL       Export through local pg_dump/psql (default: OPERAMIND_DATABASE_URL).
  --source-container NAME  Export from an existing Podman container instead.
  --source-database NAME   Database inside the source container (default: operamind).
  --evidence-root PATH     Evidence directory to archive (default: readiness/evidence).

Restore safety:
  --replace                Required acknowledgement before replacing the WSL Canonical DB.

The bundle contains Canonical PostgreSQL data, row-count verification data, and Evidence.
It never contains .env files, database passwords, API keys, or Bridge Tokens.
EOF
}

log() {
  printf '[OperaMind migration] %s\n' "$*"
}

die() {
  printf '[OperaMind migration] error: %s\n' "$*" >&2
  exit 1
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

parse_arguments() {
  (($# > 0)) || {
    usage
    exit 2
  }
  if (($# == 1)) && [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
  fi
  COMMAND="$1"
  shift
  case "${COMMAND}" in
    export | verify | restore) ;;
    *) die "Unknown command: ${COMMAND}" ;;
  esac
  while (($# > 0)); do
    case "$1" in
      --output)
        (($# >= 2)) || die "--output requires a path."
        OUTPUT="$2"
        shift
        ;;
      --bundle)
        (($# >= 2)) || die "--bundle requires a path."
        BUNDLE="$2"
        shift
        ;;
      --database-url)
        (($# >= 2)) || die "--database-url requires a value."
        SOURCE_DATABASE_URL="$2"
        DATABASE_URL_EXPLICIT=1
        shift
        ;;
      --source-container)
        (($# >= 2)) || die "--source-container requires a name."
        SOURCE_CONTAINER="$2"
        shift
        ;;
      --source-database)
        (($# >= 2)) || die "--source-database requires a name."
        SOURCE_DATABASE="$2"
        shift
        ;;
      --evidence-root)
        (($# >= 2)) || die "--evidence-root requires a path."
        EVIDENCE_ROOT="$2"
        shift
        ;;
      --replace) REPLACE=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "Unknown option: $1" ;;
    esac
    shift
  done
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"
}

safe_extract() {
  local archive="$1" destination="$2"
  mkdir -p "${destination}"
  python3 - "${archive}" "${destination}" <<'PY'
from pathlib import Path, PurePosixPath
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with tarfile.open(archive, "r:*") as bundle:
    destination = destination.resolve()
    for member in bundle.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive member: {member.name}")
        target = (destination / Path(*path.parts)).resolve()
        if not target.is_relative_to(destination):
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit(f"archive member has no content: {member.name}")
        with source, target.open("wb") as output:
            output.write(source.read())
PY
}

write_bundle_sidecar() {
  local bundle="$1" digest
  digest="$(sha256_file "${bundle}")"
  printf '%s  %s\n' "${digest}" "$(basename -- "${bundle}")" >"${bundle}.sha256"
}

verify_bundle_sidecar() {
  local bundle="$1" sidecar="${1}.sha256" expected actual
  [[ -f "${sidecar}" ]] || die "Missing bundle checksum sidecar: ${sidecar}"
  expected="$(awk 'NR == 1 {print $1}' "${sidecar}")"
  [[ "${expected}" =~ ^[0-9a-fA-F]{64}$ ]] || die "Invalid bundle checksum sidecar."
  actual="$(sha256_file "${bundle}")"
  [[ "${actual}" == "${expected}" ]] || die "Bundle checksum mismatch."
}

validate_extracted_bundle() {
  local root="$1"
  python3 - "${root}" <<'PY'
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
expected_files = {
    "manifest.json",
    "canonical.dump",
    "database-row-counts.tsv",
    "evidence.tar.gz",
}
actual_files = {
    str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
}
if actual_files != expected_files:
    raise SystemExit(f"unexpected bundle files: {sorted(actual_files ^ expected_files)}")
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("format") != "operamind-environment-bundle" or manifest.get("version") != 1:
    raise SystemExit("unsupported migration bundle format")
if manifest.get("secrets_included") is not False:
    raise SystemExit("migration bundle must not include secrets")

def digest(name: str) -> str:
    return hashlib.sha256((root / name).read_bytes()).hexdigest()

checks = {
    "canonical.dump": manifest["database"]["sha256"],
    "database-row-counts.tsv": manifest["database"]["row_counts_sha256"],
    "evidence.tar.gz": manifest["evidence"]["sha256"],
}
for name, expected in checks.items():
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or digest(name) != expected:
        raise SystemExit(f"bundle member checksum mismatch: {name}")
rows = (root / "database-row-counts.tsv").read_text(encoding="utf-8").splitlines()
if len(rows) != manifest["database"]["table_count"]:
    raise SystemExit("database table count manifest mismatch")
for row in rows:
    name, separator, count = row.rpartition("\t")
    if not separator or not name or not count.isdigit():
        raise SystemExit("invalid database row-count entry")
PY
  rm -rf -- "${root}/evidence"
  safe_extract "${root}/evidence.tar.gz" "${root}/evidence"
  local expected_count actual_count
  expected_count="$(python3 - "${root}/manifest.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["evidence"]["file_count"])
PY
)"
  actual_count="$(find "${root}/evidence" -type f | wc -l | tr -d ' ')"
  [[ "${actual_count}" == "${expected_count}" ]] || die "Evidence file-count mismatch."
}

collect_host_row_counts() {
  local database_url="$1" output="$2" tables table count
  tables="$(psql "${database_url}" -X -v ON_ERROR_STOP=1 -At -c \
    "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")"
  : >"${output}"
  while IFS= read -r table; do
    [[ -n "${table}" ]] || continue
    count="$(psql "${database_url}" -X -v ON_ERROR_STOP=1 -At -c \
      "SELECT count(*) FROM ${table}")"
    [[ "${count}" =~ ^[0-9]+$ ]] || die "Invalid row count returned for ${table}."
    printf '%s\t%s\n' "${table}" "${count}" >>"${output}"
  done <<EOF
${tables}
EOF
}

collect_container_row_counts() {
  local container="$1" database="$2" output="$3" tables table count
  tables="$(podman exec "${container}" psql -U operamind -d "${database}" \
    -X -v ON_ERROR_STOP=1 -At -c \
    "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")"
  : >"${output}"
  while IFS= read -r table; do
    [[ -n "${table}" ]] || continue
    count="$(podman exec "${container}" psql -U operamind -d "${database}" \
      -X -v ON_ERROR_STOP=1 -At -c "SELECT count(*) FROM ${table}")"
    [[ "${count}" =~ ^[0-9]+$ ]] || die "Invalid row count returned for ${table}."
    printf '%s\t%s\n' "${table}" "${count}" >>"${output}"
  done <<EOF
${tables}
EOF
}

archive_evidence() {
  local output="$1" empty
  if [[ -d "${EVIDENCE_ROOT}" ]]; then
    if [[ -n "$(find "${EVIDENCE_ROOT}" -type l -print -quit)" ]]; then
      die "Evidence root contains symbolic links; refusing to archive it."
    fi
    COPYFILE_DISABLE=1 tar -C "${EVIDENCE_ROOT}" -czf "${output}" .
    find "${EVIDENCE_ROOT}" -type f | wc -l | tr -d ' '
    return
  fi
  empty="$(mktemp -d "${TMPDIR:-/tmp}/operamind-empty.XXXXXX")"
  COPYFILE_DISABLE=1 tar -C "${empty}" -czf "${output}" .
  rm -rf -- "${empty}"
  printf '0\n'
}

export_bundle() {
  [[ -n "${OUTPUT}" ]] || die "export requires --output."
  [[ -z "${BUNDLE}" ]] || die "export does not accept --bundle."
  if [[ -n "${SOURCE_CONTAINER}" && ${DATABASE_URL_EXPLICIT} -eq 1 ]]; then
    die "Choose either --source-container or --database-url, not both."
  fi
  require_command python3
  require_command tar
  local stage output_tmp source_mode revision created_at evidence_count table_count
  local dump_sha row_counts_sha evidence_sha
  stage="$(mktemp -d "${TMPDIR:-/tmp}/operamind-export.XXXXXX")"
  output_tmp="${OUTPUT}.tmp.$$"
  trap 'rm -rf -- "${stage}" "${output_tmp}"' EXIT
  mkdir -p "$(dirname -- "${OUTPUT}")"

  if [[ -n "${SOURCE_CONTAINER}" ]]; then
    require_command podman
    podman container exists "${SOURCE_CONTAINER}" || die "Source container does not exist."
    log "Exporting Canonical DB from Podman container ${SOURCE_CONTAINER}."
    podman exec "${SOURCE_CONTAINER}" pg_dump -U operamind -d "${SOURCE_DATABASE}" \
      --format=custom --no-owner --no-acl >"${stage}/canonical.dump"
    collect_container_row_counts \
      "${SOURCE_CONTAINER}" "${SOURCE_DATABASE}" "${stage}/database-row-counts.tsv"
    source_mode="podman"
  else
    SOURCE_DATABASE_URL="${SOURCE_DATABASE_URL:-${OPERAMIND_DATABASE_URL:-}}"
    [[ -n "${SOURCE_DATABASE_URL}" ]] || die \
      "Set OPERAMIND_DATABASE_URL or pass --database-url/--source-container."
    require_command pg_dump
    require_command psql
    log "Exporting Canonical DB through local PostgreSQL client tools."
    pg_dump --dbname="${SOURCE_DATABASE_URL}" --format=custom --no-owner --no-acl \
      --file="${stage}/canonical.dump"
    collect_host_row_counts "${SOURCE_DATABASE_URL}" "${stage}/database-row-counts.tsv"
    source_mode="database_url"
  fi

  evidence_count="$(archive_evidence "${stage}/evidence.tar.gz")"
  dump_sha="$(sha256_file "${stage}/canonical.dump")"
  row_counts_sha="$(sha256_file "${stage}/database-row-counts.tsv")"
  evidence_sha="$(sha256_file "${stage}/evidence.tar.gz")"
  table_count="$(wc -l <"${stage}/database-row-counts.tsv" | tr -d ' ')"
  revision="$(git -C "${REPOSITORY_ROOT}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
  created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 - "${stage}/manifest.json" "${created_at}" "${revision}" "${source_mode}" \
    "${dump_sha}" "${row_counts_sha}" "${table_count}" "${evidence_sha}" \
    "${evidence_count}" <<'PY'
import json
from pathlib import Path
import sys

(
    target,
    created_at,
    revision,
    source_mode,
    dump_sha,
    row_counts_sha,
    table_count,
    evidence_sha,
    evidence_count,
) = sys.argv[1:]
manifest = {
    "format": "operamind-environment-bundle",
    "version": 1,
    "created_at_utc": created_at,
    "source_revision": revision,
    "source_mode": source_mode,
    "secrets_included": False,
    "database": {
        "path": "canonical.dump",
        "sha256": dump_sha,
        "row_counts_path": "database-row-counts.tsv",
        "row_counts_sha256": row_counts_sha,
        "table_count": int(table_count),
    },
    "evidence": {
        "path": "evidence.tar.gz",
        "sha256": evidence_sha,
        "file_count": int(evidence_count),
    },
}
Path(target).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  COPYFILE_DISABLE=1 tar -C "${stage}" -czf "${output_tmp}" \
    manifest.json canonical.dump database-row-counts.tsv evidence.tar.gz
  mv -- "${output_tmp}" "${OUTPUT}"
  write_bundle_sidecar "${OUTPUT}"
  trap - EXIT
  rm -rf -- "${stage}"
  log "Export complete: ${OUTPUT}"
  log "Transfer both the bundle and ${OUTPUT}.sha256 to WSL."
}

prepare_bundle() {
  local bundle="$1" stage="$2"
  [[ -f "${bundle}" ]] || die "Bundle does not exist: ${bundle}"
  require_command python3
  verify_bundle_sidecar "${bundle}"
  safe_extract "${bundle}" "${stage}"
  validate_extracted_bundle "${stage}"
}

verify_bundle() {
  [[ -n "${BUNDLE}" ]] || die "verify requires --bundle."
  local stage
  stage="$(mktemp -d "${TMPDIR:-/tmp}/operamind-verify.XXXXXX")"
  trap 'rm -rf -- "${stage}"' EXIT
  prepare_bundle "${BUNDLE}" "${stage}"
  trap - EXIT
  rm -rf -- "${stage}"
  log "Bundle verification passed: ${BUNDLE}"
}

recreate_database() {
  local container="$1" database="$2"
  podman exec "${container}" dropdb --if-exists --force -U operamind "${database}"
  podman exec "${container}" createdb -U operamind "${database}"
}

restore_dump() {
  local container="$1" database="$2" dump="$3"
  podman exec -i "${container}" pg_restore -U operamind -d "${database}" \
    --exit-on-error --no-owner --no-acl <"${dump}"
}

write_restore_receipt() {
  local stage="$1" backup="$2" receipt="$3" bundle_sha
  bundle_sha="$(sha256_file "${BUNDLE}")"
  python3 - "${stage}/manifest.json" "${receipt}" "${bundle_sha}" "${backup}" <<'PY'
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
receipt = {
    "kind": "environment_restore",
    "status": "passed",
    "restored_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    "bundle_sha256": sys.argv[3],
    "source_revision": manifest["source_revision"],
    "database_table_count": manifest["database"]["table_count"],
    "evidence_file_count": manifest["evidence"]["file_count"],
    "pre_restore_backup": sys.argv[4],
    "secrets_restored": False,
    "bridge_token_action": "register the new .env.wsl token in VS Code SecretStorage",
}
Path(sys.argv[2]).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

restore_bundle() {
  [[ -n "${BUNDLE}" ]] || die "restore requires --bundle."
  ((REPLACE == 1)) || die "restore requires --replace acknowledgement."
  require_command podman
  [[ -x "${REPOSITORY_ROOT}/.venv/bin/operamind-migrate" ]] || die \
    "Python environment is missing; run ./scripts/install-wsl.sh install first."
  operamind_load_env_file "${ENV_FILE}" || die "${ENV_FILE} is invalid."
  operamind_validate_wsl_environment || die "${ENV_FILE} failed validation."
  podman info >/dev/null || die "Podman is unavailable."
  local container="${OPERAMIND_POSTGRES_CONTAINER}" database="operamind"
  podman container exists "${container}" || die "Target PostgreSQL container does not exist."
  if [[ "$(podman inspect -f '{{.State.Running}}' "${container}")" != "true" ]]; then
    podman start "${container}" >/dev/null
  fi

  local stage timestamp backup target_counts evidence_target receipt
  stage="$(mktemp -d "${TMPDIR:-/tmp}/operamind-restore.XXXXXX")"
  trap 'rm -rf -- "${stage}"' EXIT
  prepare_bundle "${BUNDLE}" "${stage}"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "${BACKUP_ROOT}"
  backup="${BACKUP_ROOT}/pre-restore-${timestamp}.dump"
  log "Backing up the current WSL Canonical DB before replacement."
  podman exec "${container}" pg_dump -U operamind -d "${database}" \
    --format=custom --no-owner --no-acl >"${backup}"
  write_bundle_sidecar "${backup}"

  log "Restoring Canonical DB from the verified migration bundle."
  recreate_database "${container}" "${database}"
  if ! restore_dump "${container}" "${database}" "${stage}/canonical.dump"; then
    log "Restore failed; rolling back to ${backup}."
    recreate_database "${container}" "${database}"
    restore_dump "${container}" "${database}" "${backup}" || true
    die "Canonical DB restore failed; rollback was attempted."
  fi
  target_counts="${stage}/target-row-counts.tsv"
  collect_container_row_counts "${container}" "${database}" "${target_counts}"
  if ! diff -u "${stage}/database-row-counts.tsv" "${target_counts}"; then
    log "Row-count verification failed; rolling back to ${backup}."
    recreate_database "${container}" "${database}"
    restore_dump "${container}" "${database}" "${backup}" || true
    die "Restored Canonical DB did not match the exported row counts."
  fi

  evidence_target="${REPOSITORY_ROOT}/readiness/evidence"
  log "Applying and verifying immutable PostgreSQL migrations."
  if ! "${REPOSITORY_ROOT}/.venv/bin/operamind-migrate" --root "${REPOSITORY_ROOT}"; then
    log "Migration failed; rolling back to ${backup}."
    recreate_database "${container}" "${database}"
    restore_dump "${container}" "${database}" "${backup}" || true
    die "PostgreSQL migration failed; rollback was attempted."
  fi
  mkdir -p "${evidence_target}"
  cp -a "${stage}/evidence/." "${evidence_target}/"
  receipt="${evidence_target}/environment-restore-${timestamp}.json"
  write_restore_receipt "${stage}" "${backup}" "${receipt}"
  trap - EXIT
  rm -rf -- "${stage}"
  log "Restore verification passed. Receipt: ${receipt}"
  log "Register the new OPERAMIND_BRIDGE_TOKEN from .env.wsl in VS Code SecretStorage."
}

main() {
  parse_arguments "$@"
  cd -- "${REPOSITORY_ROOT}"
  case "${COMMAND}" in
    export) export_bundle ;;
    verify) verify_bundle ;;
    restore) restore_bundle ;;
  esac
}

main "$@"
