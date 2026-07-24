"""Capture real readiness observations and synchronize the repository manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from jsonschema import Draft202012Validator, FormatChecker

from operamind.application.readiness_evidence import ReadinessEvidenceSyncService
from operamind.infrastructure.postgres import (
    MigrationCatalog,
    MigrationRunner,
    ReadinessEvidenceRepository,
)
from operamind.readiness import (
    FULL_LOCAL_REGRESSION_COMMAND,
    FULL_LOCAL_REGRESSION_EXCLUDED_TESTS,
    SOURCE_TREE_DIGEST_ALGORITHM,
    MvpReadinessValidator,
)
from operamind.readiness_copilot import inspect_vscode_copilot_session

PROVIDER_LIVE_TEST_COMMAND = (
    ".venv/bin/python",
    "-m",
    "pytest",
    "-q",
    "tests/integration/test_live_embedding_provider.py",
    "tests/infrastructure/test_embedding_provider.py",
    "tests/profiles",
)
PYTEST_COUNT = re.compile(
    r"(?P<count>\d+) (?P<kind>passed|failed|skipped|xfailed|xpassed|errors?|deselected)"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automate OperaMind readiness evidence")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="derive evidence and update readiness")
    _add_scope(sync)

    record = subparsers.add_parser(
        "record-observation", help="append a reviewed external observation from JSON"
    )
    record.add_argument("--input", type=Path, required=True)

    inspect_copilot = subparsers.add_parser(
        "inspect-copilot-session",
        help="verify one VS Code GitHub Copilot JSONL request before receipt review",
    )
    inspect_copilot.add_argument("--input", type=Path, required=True)
    inspect_copilot.add_argument("--request-id", required=True)

    record_copilot = subparsers.add_parser(
        "record-copilot-session",
        help="bind a reviewed VS Code Copilot request to one completed Coding Task",
    )
    _add_scope(record_copilot)
    record_copilot.add_argument("--coding-task-id", required=True)
    record_copilot.add_argument("--input", type=Path, required=True)
    record_copilot.add_argument("--request-id", required=True)
    record_copilot.add_argument("--reviewed-by", required=True)

    provider = subparsers.add_parser(
        "probe-provider", help="run the fixed live provider tests, record, and sync"
    )
    _add_scope(provider)
    provider.add_argument("--profile-version-id", required=True)
    provider.add_argument("--model", required=True)
    provider.add_argument("--dimensions", required=True, type=int)
    provider.add_argument("--endpoint-origin", required=True)

    regression = subparsers.add_parser(
        "run-full-regression", help="run the fixed full suite, record, and sync"
    )
    _add_scope(regression)
    return parser


def _add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--analysis-case-id", required=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    if args.command == "inspect-copilot-session":
        try:
            result = inspect_vscode_copilot_session(
                args.input,
                request_id=args.request_id,
            )
        except (OSError, ValueError) as error:
            print(f"error: {error}")
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required")
        return 2
    with psycopg.connect(database_url) as connection:
        MigrationRunner(connection, MigrationCatalog.load(root / "migrations")).apply()
        repository = ReadinessEvidenceRepository(connection)
        if args.command == "record-copilot-session":
            session = inspect_vscode_copilot_session(
                args.input,
                request_id=args.request_id,
            )
            subject = repository.copilot_task_receipt_subject(args.coding_task_id)
            if subject["project_id"] != args.project_id:
                raise ValueError("Copilot Coding Task is outside requested Project")
            if subject["analysis_case_id"] != args.analysis_case_id:
                raise ValueError("Copilot Coding Task is outside requested Analysis Case")
            subject.update(
                {
                    "mcp_protocol_version": "2025-11-25",
                    "tool_approval_status": "confirmed",
                    "vscode_session_id": session["vscode_session_id"],
                    "vscode_request_id": session["request_id"],
                    "vscode_response_id": session["response_id"],
                    "copilot_extension_version": session["copilot_extension_version"],
                    "copilot_model_id": session["copilot_model_id"],
                    "session_transcript_sha256": session["session_transcript_sha256"],
                    "completed_mcp_tools": session["completed_mcp_tools"],
                }
            )
            completed_at = datetime.fromisoformat(str(session["completed_at"]))
            reviewer = str(args.reviewed_by).strip()
            if not reviewer:
                raise ValueError("--reviewed-by must not be blank")
            observation_id = (
                "copilot-session:"
                + hashlib.sha256(
                    (
                        f"{args.coding_task_id}\0{session['vscode_session_id']}\0"
                        f"{session['request_id']}\0{session['session_transcript_sha256']}"
                    ).encode()
                ).hexdigest()
            )
            payload = _observation_payload(
                gate_id="github_copilot_live",
                evidence_type="copilot_session",
                project_id=args.project_id,
                analysis_case_id=args.analysis_case_id,
                observed_at=completed_at,
                review_status="reviewed",
                reviewed_by=(reviewer,),
                subject=subject,
                observation_id=observation_id,
            )
            _validate_observation(root, payload)
            created = _record_payload(repository, payload)
            print(
                "Copilot session observation recorded"
                if created
                else "Copilot session observation replayed"
            )
            return _print_sync(
                ReadinessEvidenceSyncService(repository_root=root, source=repository).sync(
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                )
            )
        if args.command == "record-observation":
            payload = _load_object(args.input.resolve())
            _validate_observation(root, payload)
            created = _record_payload(repository, payload)
            print("Readiness observation recorded" if created else "Readiness observation replayed")
            return 0
        service = ReadinessEvidenceSyncService(repository_root=root, source=repository)
        if args.command == "sync":
            return _print_sync(
                service.sync(
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                )
            )
        if args.command == "probe-provider":
            completed = _run(root, PROVIDER_LIVE_TEST_COMMAND)
            if completed.returncode != 0:
                print("Provider probe failed; no passing observation was recorded")
                return completed.returncode or 1
            observed_at = datetime.now(UTC)
            payload = _observation_payload(
                gate_id="embedding_provider_live",
                evidence_type="provider_probe",
                project_id=args.project_id,
                analysis_case_id=None,
                observed_at=observed_at,
                review_status="verified",
                reviewed_by=("automation:operamind-readiness",),
                subject={
                    "profile_version_id": args.profile_version_id,
                    "model": args.model,
                    "dimensions": args.dimensions,
                    "endpoint_origin": args.endpoint_origin,
                    "test_command": list(PROVIDER_LIVE_TEST_COMMAND),
                    "exit_code": 0,
                },
            )
            _validate_observation(root, payload)
            _record_payload(repository, payload)
            return _print_sync(
                service.sync(
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                )
            )
        if args.command == "run-full-regression":
            # Publish pending first so stale historical evidence cannot make its own rerun fail.
            service.sync(project_id=args.project_id, analysis_case_id=args.analysis_case_id)
            source_digest = MvpReadinessValidator.source_tree_digest(root)
            completed = _run(root, FULL_LOCAL_REGRESSION_COMMAND)
            counts = _pytest_counts(completed.stdout + "\n" + completed.stderr)
            if (
                completed.returncode != 0
                or counts["failed"]
                or counts["skipped"]
                or counts["unexpected"]
            ):
                print("Full regression did not pass cleanly; readiness remains pending")
                return completed.returncode or 1
            payload = _observation_payload(
                gate_id="full_local_regression",
                evidence_type="test_report",
                project_id=None,
                analysis_case_id=None,
                observed_at=datetime.now(UTC),
                review_status="verified",
                reviewed_by=("automation:operamind-readiness",),
                subject={
                    "source_tree_algorithm": SOURCE_TREE_DIGEST_ALGORITHM,
                    "source_tree_sha256": source_digest,
                    "test_command": list(FULL_LOCAL_REGRESSION_COMMAND),
                    "excluded_tests": list(FULL_LOCAL_REGRESSION_EXCLUDED_TESTS),
                    "collected": counts["passed"],
                    "passed": counts["passed"],
                    "failed": 0,
                    "skipped": 0,
                    "database_version": repository.database_version(),
                    "browser_version": _browser_version(),
                },
            )
            _validate_observation(root, payload)
            _record_payload(repository, payload)
            return _print_sync(
                service.sync(
                    project_id=args.project_id,
                    analysis_case_id=args.analysis_case_id,
                )
            )
    raise AssertionError("Unreachable readiness command")


def _run(root: Path, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    return completed


def _pytest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "unexpected": 0}
    for match in PYTEST_COUNT.finditer(output):
        kind = match.group("kind")
        key = kind if kind in {"passed", "failed", "skipped"} else "unexpected"
        counts[key] += int(match.group("count"))
    if counts["passed"] < 1:
        raise ValueError("Pytest output did not report any passed tests")
    return counts


def _browser_version() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            return f"Chromium {browser.version}"
        finally:
            browser.close()


def _observation_payload(
    *,
    gate_id: str,
    evidence_type: str,
    project_id: str | None,
    analysis_case_id: str | None,
    observed_at: datetime,
    review_status: str,
    reviewed_by: tuple[str, ...],
    subject: dict[str, object],
    observation_id: str | None = None,
) -> dict[str, object]:
    digest = hashlib.sha256(
        json.dumps(subject, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "observation_id": observation_id or f"readiness-{gate_id}-{digest[:24]}",
        "gate_id": gate_id,
        "evidence_type": evidence_type,
        "project_id": project_id,
        "analysis_case_id": analysis_case_id,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "review_status": review_status,
        "reviewed_by": list(reviewed_by),
        "subject": subject,
    }


def _validate_observation(root: Path, payload: dict[str, object]) -> None:
    required = {
        "observation_id",
        "gate_id",
        "evidence_type",
        "project_id",
        "analysis_case_id",
        "observed_at",
        "review_status",
        "reviewed_by",
        "subject",
    }
    if set(payload) != required:
        raise ValueError(f"Observation fields mismatch: {sorted(set(payload) ^ required)}")
    envelope = {
        "evidence_format_version": "v1",
        "evidence_id": payload["observation_id"],
        "gate_id": payload["gate_id"],
        "evidence_type": payload["evidence_type"],
        "outcome": "passed",
        "observed_at": payload["observed_at"],
        "review_status": payload["review_status"],
        "reviewed_by": payload["reviewed_by"],
        "subject": payload["subject"],
    }
    schema = _load_object(root / "readiness/mvp-evidence.schema.json")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(envelope),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ValueError("; ".join(error.message for error in errors))
    blocked = ("replace-with", "placeholder", "example.invalid")

    def has_placeholder(value: object) -> bool:
        if isinstance(value, str):
            return any(token in value.lower() for token in blocked)
        if isinstance(value, dict):
            return any(has_placeholder(child) for child in value.values())
        if isinstance(value, list):
            return any(has_placeholder(child) for child in value)
        return False

    if has_placeholder(envelope):
        raise ValueError("Readiness observation still contains a template placeholder")


def _record_payload(repository: ReadinessEvidenceRepository, payload: dict[str, object]) -> bool:
    reviewed_by = payload["reviewed_by"]
    subject = payload["subject"]
    if not isinstance(reviewed_by, list) or not isinstance(subject, dict):
        raise ValueError("Observation reviewed_by and subject have invalid types")
    return repository.record_observation(
        observation_id=str(payload["observation_id"]),
        gate_id=str(payload["gate_id"]),
        evidence_type=str(payload["evidence_type"]),
        project_id=str(payload["project_id"]) if payload["project_id"] is not None else None,
        analysis_case_id=(
            str(payload["analysis_case_id"]) if payload["analysis_case_id"] is not None else None
        ),
        observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        review_status=str(payload["review_status"]),
        reviewed_by=tuple(str(value) for value in reviewed_by),
        subject={str(key): value for key, value in subject.items()},
    )


def _load_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _print_sync(result: object) -> int:
    from operamind.application.readiness_evidence import ReadinessSyncResult

    if not isinstance(result, ReadinessSyncResult):
        raise TypeError("Unexpected readiness sync result")
    print(
        f"Readiness synchronized: changed={str(result.changed).lower()} "
        f"manifest_version={result.manifest_version} stage={result.summary.readiness_stage}"
    )
    print(f"Passed gates: {', '.join(result.summary.passed_gates) or '(none)'}")
    print(f"Pending gates: {', '.join(result.summary.pending_gates) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
