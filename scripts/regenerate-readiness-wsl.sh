#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ID="${1:-}"
ANALYSIS_CASE_ID="${2:-}"

if [[ -z "${PROJECT_ID}" || -z "${ANALYSIS_CASE_ID}" ]]; then
  printf 'Usage: bash scripts/regenerate-readiness-wsl.sh PROJECT_ID ANALYSIS_CASE_ID\n' >&2
  exit 2
fi
if [[ ! -r /proc/sys/kernel/osrelease ]] || ! grep -qi microsoft /proc/sys/kernel/osrelease; then
  printf 'error: run this command inside WSL2.\n' >&2
  exit 2
fi
command -v podman >/dev/null || {
  printf 'error: podman is required in WSL2.\n' >&2
  exit 2
}
command -v msedge >/dev/null || command -v microsoft-edge >/dev/null || {
  printf 'error: Microsoft Edge is required for the Playwright msedge channel.\n' >&2
  exit 2
}

# The loader parses strict KEY=VALUE files without evaluating shell syntax.
# shellcheck source=scripts/lib/operamind-env.sh
source "${SCRIPT_DIR}/lib/operamind-env.sh"
operamind_load_env_file "${REPOSITORY_ROOT}/.env.wsl"
operamind_validate_wsl_environment
cd "${REPOSITORY_ROOT}"

"${REPOSITORY_ROOT}/.venv/bin/operamind-readiness" run-full-regression \
  --root "${REPOSITORY_ROOT}" \
  --project-id "${PROJECT_ID}" \
  --analysis-case-id "${ANALYSIS_CASE_ID}"
