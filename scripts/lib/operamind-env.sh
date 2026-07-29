#!/usr/bin/env bash

# Trusted helpers for parsing OperaMind environment files without evaluating them.

operamind_env_key_is_allowed() {
  case "$1" in
    EMBED_API_KEY | EMBED_API_URL | EMBED_KEY | EMBED_MODEL | EMBED_URL | \
      OPERAMIND_BRIDGE_TOKEN | OPERAMIND_DATABASE_URL | OPERAMIND_EMBEDDING_LIVE | \
      OPERAMIND_EMBEDDING_LIVE_PROFILE | OPERAMIND_HANDLER_MODE | \
      OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN | OPERAMIND_PLAYWRIGHT_LIVE | \
      OPERAMIND_POSTGRES_CONTAINER | OPERAMIND_POSTGRES_IMAGE | \
      OPERAMIND_POSTGRES_PASSWORD | OPERAMIND_POSTGRES_PORT | \
      OPERAMIND_TEST_DATABASE_URL | OPERAMIND_TEST_TARGET_BASE_URL | \
      OPERAMIND_WORKER_TOKEN)
      return 0
      ;;
    *) return 1 ;;
  esac
}

operamind_load_env_file() {
  local env_file="$1" line key value line_number=0 seen='|'
  [[ -f "${env_file}" ]] || {
    printf 'Environment file does not exist: %s\n' "${env_file}" >&2
    return 1
  }
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line_number=$((line_number + 1))
    line="${line%$'\r'}"
    if [[ -z "${line}" || "${line}" == \#* ]]; then
      continue
    fi
    if [[ "${line}" != *"="* ]]; then
      printf 'Invalid environment entry at %s:%d; expected KEY=VALUE.\n' \
        "${env_file}" "${line_number}" >&2
      return 1
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ ! "${key}" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
      printf 'Invalid environment key at %s:%d: %s\n' \
        "${env_file}" "${line_number}" "${key}" >&2
      return 1
    fi
    if ! operamind_env_key_is_allowed "${key}"; then
      printf 'Unsupported environment key at %s:%d: %s\n' \
        "${env_file}" "${line_number}" "${key}" >&2
      return 1
    fi
    if [[ "${seen}" == *"|${key}|"* ]]; then
      printf 'Duplicate environment key at %s:%d: %s\n' \
        "${env_file}" "${line_number}" "${key}" >&2
      return 1
    fi
    if [[ "${value}" == *$'\r'* ]]; then
      printf 'Invalid carriage return in environment value at %s:%d.\n' \
        "${env_file}" "${line_number}" >&2
      return 1
    fi
    seen="${seen}${key}|"
    export "${key}=${value}"
  done <"${env_file}"
}

operamind_require_env_value() {
  local key="$1" value
  value="$(printenv "${key}" 2>/dev/null || true)"
  if [[ -z "${value}" ]]; then
    printf 'Required environment value is missing: %s\n' "${key}" >&2
    return 1
  fi
}

operamind_validate_wsl_environment() {
  local key
  for key in \
    OPERAMIND_DATABASE_URL \
    OPERAMIND_TEST_DATABASE_URL \
    OPERAMIND_BRIDGE_TOKEN \
    OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN \
    OPERAMIND_POSTGRES_CONTAINER \
    OPERAMIND_POSTGRES_PORT \
    OPERAMIND_POSTGRES_PASSWORD; do
    operamind_require_env_value "${key}" || return 1
  done
  [[ "${OPERAMIND_DATABASE_URL}" == postgresql://* ]] || {
    printf 'OPERAMIND_DATABASE_URL must use postgresql://.\n' >&2
    return 1
  }
  [[ "${OPERAMIND_TEST_DATABASE_URL}" == postgresql://* ]] || {
    printf 'OPERAMIND_TEST_DATABASE_URL must use postgresql://.\n' >&2
    return 1
  }
  [[ "${OPERAMIND_BRIDGE_TOKEN}" =~ ^[A-Za-z0-9._~+-]+$ ]] \
    && ((${#OPERAMIND_BRIDGE_TOKEN} >= 32 && ${#OPERAMIND_BRIDGE_TOKEN} <= 256)) || {
    printf 'OPERAMIND_BRIDGE_TOKEN must be a 32-256 character opaque token.\n' >&2
    return 1
  }
  [[ "${OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN}" =~ ^[1-9][0-9]*$ ]] || {
    printf 'OPERAMIND_MAX_ACTIVE_TASKS_PER_RUN must be a positive integer.\n' >&2
    return 1
  }
  [[ "${OPERAMIND_POSTGRES_CONTAINER}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || {
    printf 'OPERAMIND_POSTGRES_CONTAINER contains unsupported characters.\n' >&2
    return 1
  }
  [[ "${OPERAMIND_POSTGRES_PORT}" =~ ^[0-9]+$ ]] \
    && ((OPERAMIND_POSTGRES_PORT >= 1 && OPERAMIND_POSTGRES_PORT <= 65535)) || {
      printf 'OPERAMIND_POSTGRES_PORT must be between 1 and 65535.\n' >&2
      return 1
    }
  [[ "${OPERAMIND_POSTGRES_PASSWORD}" != *[[:space:]]* ]] || {
    printf 'OPERAMIND_POSTGRES_PASSWORD must not contain whitespace.\n' >&2
    return 1
  }
}
