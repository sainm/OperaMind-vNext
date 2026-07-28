#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV="${REPOSITORY_ROOT}/.venv"
ENV_FILE="${REPOSITORY_ROOT}/.env.wsl"
LOCAL_TOOLS="${REPOSITORY_ROOT}/.local-tools"
POSTGRES_IMAGE="${OPERAMIND_POSTGRES_IMAGE:-docker.io/pgvector/pgvector:0.8.2-pg18-bookworm}"

# shellcheck source=scripts/lib/operamind-env.sh
source "${SCRIPT_DIR}/lib/operamind-env.sh"

COMMAND="install"
DRY_RUN=0
SKIP_SYSTEM_PACKAGES=0
SKIP_BROWSER=0
SKIP_VSIX=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install-wsl.sh [install|start|stop|status] [options]

Commands:
  install                Install WSL dependencies and initialize OperaMind (default).
  start                  Start PostgreSQL, migrate, and run the Web UI in foreground.
  stop                   Stop the Podman PostgreSQL container.
  status                 Show Podman and OperaMind PostgreSQL status.

Options:
  --dry-run              Print installation actions without changing the machine.
  --skip-system-packages Do not run apt-get.
  --skip-browser         Do not install or smoke-test Playwright Chromium/Edge.
  --skip-vsix            Do not install local Node 22 or build the VSIX.
  -h, --help             Show this help.

Supported baseline: WSL2 with Ubuntu 24.04 or another apt-based distribution
that already provides Python 3.12 or newer.
EOF
}

log() {
  printf '[OperaMind WSL] %s\n' "$*"
}

die() {
  printf '[OperaMind WSL] error: %s\n' "$*" >&2
  exit 1
}

run() {
  if ((DRY_RUN)); then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

is_wsl() {
  [[ -r /proc/sys/kernel/osrelease ]] && grep -qi microsoft /proc/sys/kernel/osrelease
}

require_wsl() {
  if ((DRY_RUN)); then
    return
  fi
  is_wsl || die "Run this script inside WSL2, not in Windows PowerShell or a native Linux host."
  command -v apt-get >/dev/null || die "This installer currently requires an apt-based WSL distribution."
  if [[ "${REPOSITORY_ROOT}" == /mnt/[a-zA-Z]/* ]]; then
    die "Clone the repository under the WSL filesystem (for example ~/src), not under /mnt/c."
  fi
}

parse_arguments() {
  if (($# > 0)) && [[ "$1" != -* ]]; then
    COMMAND="$1"
    shift
  fi
  case "${COMMAND}" in
    install | start | stop | status) ;;
    *) die "Unknown command: ${COMMAND}" ;;
  esac
  while (($# > 0)); do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      --skip-system-packages) SKIP_SYSTEM_PACKAGES=1 ;;
      --skip-browser) SKIP_BROWSER=1 ;;
      --skip-vsix) SKIP_VSIX=1 ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "Unknown option: $1" ;;
    esac
    shift
  done
}

install_system_packages() {
  if ((SKIP_SYSTEM_PACKAGES)); then
    log "Skipping apt packages."
    return
  fi
  command -v sudo >/dev/null || die "sudo is required to install WSL packages."
  log "Installing Python, Podman, rootless-container, and build dependencies."
  run sudo apt-get update
  run sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential \
    ca-certificates \
    curl \
    fuse-overlayfs \
    git \
    openssl \
    podman \
    python3 \
    python3-dev \
    python3-venv \
    slirp4netns \
    uidmap \
    xz-utils
}

require_python_312() {
  if ((DRY_RUN)); then
    log "Would verify Python >= 3.12."
    return
  fi
  command -v python3 >/dev/null || die "python3 is not installed."
  python3 - <<'PY' || exit 1
import sys

if sys.version_info < (3, 12):
    raise SystemExit(
        "OperaMind requires Python 3.12+. Use Ubuntu 24.04 WSL or install Python 3.12 first."
    )
PY
}

install_python_project() {
  log "Creating the Python environment and installing OperaMind development dependencies."
  if [[ ! -x "${VENV}/bin/python" ]]; then
    run python3 -m venv "${VENV}"
  fi
  run "${VENV}/bin/python" -m pip install --upgrade pip setuptools wheel
  run "${VENV}/bin/python" -m pip install -r "${REPOSITORY_ROOT}/requirements.lock"
  run "${VENV}/bin/python" -m pip install --no-deps -e "${REPOSITORY_ROOT}"
}

install_playwright() {
  if ((SKIP_BROWSER)); then
    log "Skipping Playwright browser installation."
    return
  fi
  if ((DRY_RUN == 0)) && [[ "$(uname -m)" != "x86_64" ]]; then
    die "The Linux Microsoft Edge channel requires an x86_64 WSL distribution."
  fi
  log "Installing Playwright Chromium and Microsoft Edge for the project's msedge channel."
  run "${VENV}/bin/playwright" install --with-deps chromium msedge
}

node_major() {
  "$1" -p "Number(process.versions.node.split('.')[0])" 2>/dev/null || true
}

node_version() {
  "$1" -p "process.versions.node" 2>/dev/null || true
}

install_local_node_22() {
  local system_node="" major="" version=""
  if command -v node >/dev/null; then
    system_node="$(command -v node)"
    major="$(node_major "${system_node}")"
    version="$(node_version "${system_node}")"
    if [[ "${major}" != "22" || "${version}" != "22.14.0" ]]; then
      system_node=""
    fi
  fi
  if [[ -n "${system_node}" ]]; then
    NODE_BIN_DIR="$(dirname -- "${system_node}")"
    return
  fi
  if [[ -x "${LOCAL_TOOLS}/node/bin/node" ]]; then
    major="$(node_major "${LOCAL_TOOLS}/node/bin/node")"
    version="$(node_version "${LOCAL_TOOLS}/node/bin/node")"
    if [[ "${major}" == "22" && "${version}" == "22.14.0" ]]; then
      NODE_BIN_DIR="${LOCAL_TOOLS}/node/bin"
      return
    fi
  fi
  if ((DRY_RUN)); then
    log "Would download and checksum Node.js 22.14.0 from nodejs.org."
    NODE_BIN_DIR="${LOCAL_TOOLS}/node/bin"
    return
  fi

  local architecture archive checksum checksums temporary extracted
  local node_version="22.14.0"
  case "$(uname -m)" in
    x86_64) architecture="x64" ;;
    aarch64 | arm64) architecture="arm64" ;;
    *) die "Unsupported Node.js architecture: $(uname -m)" ;;
  esac
  checksums="$(curl -fsSL "https://nodejs.org/dist/v${node_version}/SHASUMS256.txt")"
  archive="node-v${node_version}-linux-${architecture}.tar.xz"
  checksum="$(printf '%s\n' "${checksums}" | awk -v file="${archive}" '$2 == file {print $1}')"
  [[ -n "${archive}" && -n "${checksum}" ]] || die "Could not resolve the Node.js 22 archive."

  temporary="$(mktemp -d)"
  curl -fL "https://nodejs.org/dist/v${node_version}/${archive}" -o "${temporary}/${archive}"
  printf '%s  %s\n' "${checksum}" "${temporary}/${archive}" | sha256sum -c -
  mkdir -p "${LOCAL_TOOLS}"
  tar -xJf "${temporary}/${archive}" -C "${temporary}"
  extracted="${temporary}/${archive%.tar.xz}"
  rm -rf -- "${LOCAL_TOOLS}/node-version"
  mv -- "${extracted}" "${LOCAL_TOOLS}/node-version"
  ln -sfn node-version "${LOCAL_TOOLS}/node"
  rm -rf -- "${temporary}"
  NODE_BIN_DIR="${LOCAL_TOOLS}/node/bin"
}

build_vscode_extension() {
  if ((SKIP_VSIX)); then
    log "Skipping VSIX build."
    return
  fi
  log "Preparing Node.js 22 and building the VS Code Copilot Bridge VSIX."
  install_local_node_22
  run env PATH="${NODE_BIN_DIR}:${PATH}" npm --prefix "${REPOSITORY_ROOT}/vscode-extension" ci
  run env PATH="${NODE_BIN_DIR}:${PATH}" npm --prefix "${REPOSITORY_ROOT}/vscode-extension" run package:vsix
}

create_environment_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    log "Keeping existing ${ENV_FILE}."
    return
  fi
  if ((DRY_RUN)); then
    log "Would create ${ENV_FILE} with generated PostgreSQL and Bridge secrets."
    return
  fi
  local password bridge_token
  password="$(openssl rand -hex 24)"
  bridge_token="$(openssl rand -hex 32)"
  umask 077
  cat >"${ENV_FILE}" <<EOF
OPERAMIND_DATABASE_URL=postgresql://operamind:${password}@127.0.0.1:5432/operamind
OPERAMIND_TEST_DATABASE_URL=postgresql://operamind:${password}@127.0.0.1:5432/operamind_test
OPERAMIND_BRIDGE_TOKEN=${bridge_token}
OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1
OPERAMIND_POSTGRES_CONTAINER=operamind-postgres
OPERAMIND_POSTGRES_PORT=5432
OPERAMIND_POSTGRES_PASSWORD=${password}
EOF
  chmod 600 "${ENV_FILE}"
}

load_environment() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    if ((DRY_RUN)); then
      export OPERAMIND_POSTGRES_CONTAINER=operamind-postgres
      export OPERAMIND_POSTGRES_PORT=5432
      export OPERAMIND_POSTGRES_PASSWORD=DRY_RUN_SECRET
      export OPERAMIND_DATABASE_URL=postgresql://operamind:DRY_RUN_SECRET@127.0.0.1:5432/operamind
      export OPERAMIND_TEST_DATABASE_URL=postgresql://operamind:DRY_RUN_SECRET@127.0.0.1:5432/operamind_test
      export OPERAMIND_BRIDGE_TOKEN=DRY_RUN_SECRET_DRY_RUN_SECRET_0001
      export OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN=1
      return
    fi
    die "Missing ${ENV_FILE}; run the install command first."
  fi
  operamind_load_env_file "${ENV_FILE}" || die "${ENV_FILE} is invalid."
  operamind_validate_wsl_environment || die "${ENV_FILE} failed validation."
  if [[ -n "${OPERAMIND_POSTGRES_IMAGE:-}" ]]; then
    POSTGRES_IMAGE="${OPERAMIND_POSTGRES_IMAGE}"
  fi
}

require_podman() {
  if ((DRY_RUN)); then
    return
  fi
  command -v podman >/dev/null || die "podman is not installed."
  podman info >/dev/null || die "Rootless Podman is unavailable; check WSL user namespaces."
}

ensure_postgres() {
  load_environment
  require_podman
  local container="${OPERAMIND_POSTGRES_CONTAINER:-operamind-postgres}"
  local port="${OPERAMIND_POSTGRES_PORT:-5432}"

  if ((DRY_RUN)); then
    log "Would start ${POSTGRES_IMAGE} as ${container} on 127.0.0.1:${port}."
    return
  fi
  if podman container exists "${container}"; then
    if [[ "$(podman inspect -f '{{.State.Running}}' "${container}")" != "true" ]]; then
      podman start "${container}" >/dev/null
    fi
  else
    podman volume create operamind-postgres-data >/dev/null
    podman run -d \
      --name "${container}" \
      --restart=unless-stopped \
      -e POSTGRES_USER=operamind \
      -e POSTGRES_PASSWORD="${OPERAMIND_POSTGRES_PASSWORD}" \
      -e POSTGRES_DB=operamind \
      -p "127.0.0.1:${port}:5432" \
      -v operamind-postgres-data:/var/lib/postgresql/data \
      "${POSTGRES_IMAGE}" >/dev/null
  fi

  local ready=0
  for _ in $(seq 1 60); do
    if podman exec "${container}" pg_isready -U operamind -d operamind >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  ((ready)) || {
    podman logs --tail 100 "${container}" >&2
    die "PostgreSQL did not become ready within 60 seconds."
  }

  if [[ "$(podman exec "${container}" psql -U operamind -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'operamind_test'")" != "1" ]]; then
    podman exec "${container}" createdb -U operamind operamind_test
  fi
}

migrate_database() {
  load_environment
  log "Applying immutable PostgreSQL migrations."
  run "${VENV}/bin/operamind-migrate" --root "${REPOSITORY_ROOT}"
}

smoke_test() {
  if ((DRY_RUN)); then
    log "Would smoke-test pgvector and headless Playwright."
    return
  fi
  local container="${OPERAMIND_POSTGRES_CONTAINER:-operamind-postgres}"
  podman exec "${container}" psql -U operamind -d operamind -tAc \
    "SELECT extversion FROM pg_extension WHERE extname = 'vector'" | grep -Eq '^[0-9]+[.]'
  if ((SKIP_BROWSER == 0)); then
    "${VENV}/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    browser.close()
PY
  fi
}

install_all() {
  install_system_packages
  require_python_312
  install_python_project
  install_playwright
  build_vscode_extension
  create_environment_file
  ensure_postgres
  migrate_database
  smoke_test
  log "Installation complete. Start OperaMind with: ./scripts/install-wsl.sh start"
  log "Install vscode-extension/dist/operamind-copilot-bridge.vsix from Windows VS Code."
}

start_all() {
  [[ -x "${VENV}/bin/operamind-web" ]] || die "Python environment is missing; run install first."
  ensure_postgres
  migrate_database
  load_environment
  log "Starting OperaMind Web at http://127.0.0.1:8765 (Ctrl+C stops Web only)."
  exec "${VENV}/bin/operamind-web" --root "${REPOSITORY_ROOT}" --host 127.0.0.1 --port 8765
}

stop_all() {
  load_environment
  require_podman
  local container="${OPERAMIND_POSTGRES_CONTAINER:-operamind-postgres}"
  if ((DRY_RUN)); then
    log "Would stop ${container}."
  elif podman container exists "${container}"; then
    podman stop "${container}" >/dev/null
    log "Stopped ${container}."
  else
    log "Container ${container} does not exist."
  fi
}

show_status() {
  load_environment
  require_podman
  local container="${OPERAMIND_POSTGRES_CONTAINER:-operamind-postgres}"
  run podman ps -a --filter "name=^${container}$"
  if ((DRY_RUN == 0)) && podman container exists "${container}" \
    && [[ "$(podman inspect -f '{{.State.Running}}' "${container}")" == "true" ]]; then
    podman exec "${container}" pg_isready -U operamind -d operamind
  fi
}

main() {
  parse_arguments "$@"
  require_wsl
  cd -- "${REPOSITORY_ROOT}"
  case "${COMMAND}" in
    install) install_all ;;
    start) start_all ;;
    stop) stop_all ;;
    status) show_status ;;
  esac
}

main "$@"
