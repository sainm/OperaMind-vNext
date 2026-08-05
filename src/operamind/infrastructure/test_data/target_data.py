"""Project-bound target database profiles and owner-only connection secrets."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from operamind.application.data_identity import (
    is_sensitive_data_identity_name,
    redact_secret_evidence,
)
from operamind.application.test_data_execution import (
    TestDataExecutionEvidence,
    TestDataExecutionRequest,
    TestDataStepBlockedError,
    TestDataStepExecution,
)
from operamind.infrastructure.browser import LocalEvidenceStore
from operamind.infrastructure.test_data.target_database import (
    TargetDatabaseAdapterRegistry,
    TargetDatabaseExecutionError,
    default_target_database_adapters,
)
from operamind.local_installation import installation_paths

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RAW_SQL_INPUT_KEYS = frozenset({"sql", "query", "statement", "statement_text"})
_INPUT_TYPES = frozenset({"string", "integer", "number", "boolean"})
_IDEMPOTENCY_POLICIES = frozenset(
    {"natural_key", "upsert", "delete_then_insert", "read_only"}
)


@dataclass(frozen=True, slots=True)
class TargetDataBinding:
    project_id: str
    connection_alias: str
    dialect: str
    query_binding_id: str
    operation: str
    statement_text: str
    target_schema: str
    target_table: str
    parameter_columns: Mapping[str, str]
    input_constraints: Mapping[str, Mapping[str, object]]
    read_after_write_statement: str
    read_assertion: Mapping[str, object]
    identity_contract: Mapping[str, object]
    cleanup_binding_id: str | None
    idempotency_policy: str

    def public_view(self, *, include_statements: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "query_binding_id": self.query_binding_id,
            "operation": self.operation,
            "target_schema": self.target_schema,
            "target_table": self.target_table,
            "parameter_columns": dict(self.parameter_columns),
            "input_constraints": {
                key: dict(value) for key, value in self.input_constraints.items()
            },
            "read_assertion": dict(self.read_assertion),
            "identity_contract": dict(self.identity_contract),
            "cleanup_binding_id": self.cleanup_binding_id,
            "idempotency_policy": self.idempotency_policy,
        }
        if include_statements:
            result["statement_text"] = self.statement_text
            result["read_after_write_statement"] = self.read_after_write_statement
        return result


@dataclass(frozen=True, slots=True)
class TargetDataProfile:
    project_id: str
    connection_alias: str
    dialect: str
    transaction_policy: str
    reviewed_by: str
    reviewed_at: datetime
    bindings: tuple[TargetDataBinding, ...]

    def public_view(self, *, include_statements: bool = False) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "connection_alias": self.connection_alias,
            "dialect": self.dialect,
            "transaction_policy": self.transaction_policy,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat(),
            "secret_configured": None,
            "bindings": [
                binding.public_view(include_statements=include_statements)
                for binding in self.bindings
            ],
        }


class TargetDataSecretStore:
    """Persist target connection strings outside workspaces and the Canonical DB."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        adapters: TargetDatabaseAdapterRegistry | None = None,
    ) -> None:
        self._root = (root or installation_paths().data_directory / "target-data-secrets").resolve()
        self._adapters = adapters or default_target_database_adapters()

    def put(
        self,
        *,
        project_id: str,
        connection_alias: str,
        connection_dsn: str,
        dialect: str = "postgresql",
    ) -> None:
        self._adapters.require(dialect).validate_connection_secret(connection_dsn)
        self._ensure_root()
        destination = self._path(project_id, connection_alias)
        if destination.is_symlink():
            raise ValueError("Target Data secret path must not be a symbolic link")
        temporary = destination.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(connection_dsn.strip() + "\n", encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(destination)

    def get(
        self,
        *,
        project_id: str,
        connection_alias: str,
        dialect: str = "postgresql",
    ) -> str:
        path = self._path(project_id, connection_alias)
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Target Data connection secret is not configured for alias: {connection_alias}"
            )
        value = path.read_text(encoding="utf-8").strip()
        self._adapters.require(dialect).validate_connection_secret(value)
        return value

    def configured(
        self,
        *,
        project_id: str,
        connection_alias: str,
        dialect: str = "postgresql",
    ) -> bool:
        try:
            self.get(
                project_id=project_id,
                connection_alias=connection_alias,
                dialect=dialect,
            )
        except ValueError:
            return False
        return True

    def delete(self, *, project_id: str, connection_alias: str) -> None:
        path = self._path(project_id, connection_alias)
        if path.is_file() and not path.is_symlink():
            path.unlink()

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self._root.chmod(0o700)

    def _path(self, project_id: str, connection_alias: str) -> Path:
        if not project_id.strip() or not connection_alias.strip():
            raise ValueError("Target Data secret identity must not be blank")
        digest = hashlib.sha256(f"{project_id}\0{connection_alias}".encode()).hexdigest()
        return self._root / f"{digest}.secret"


class TargetDataProfileRepository:
    """Canonical non-secret Target Data Profile configuration."""

    def __init__(
        self,
        connection: Connection[Any],
        *,
        adapters: TargetDatabaseAdapterRegistry | None = None,
    ) -> None:
        self._connection = connection
        self._adapters = adapters or default_target_database_adapters()

    def replace(
        self,
        *,
        project_id: str,
        connection_alias: str,
        dialect: str = "postgresql",
        transaction_policy: str,
        bindings: Sequence[Mapping[str, object]],
        reviewed_by: str,
    ) -> TargetDataProfile:
        _validate_profile_input(
            project_id=project_id,
            connection_alias=connection_alias,
            dialect=dialect,
            transaction_policy=transaction_policy,
            bindings=bindings,
            reviewed_by=reviewed_by,
            adapters=self._adapters,
        )
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM projects WHERE project_id = %s FOR UPDATE", (project_id,)
            )
            if cursor.fetchone() is None:
                raise ValueError("Project does not exist")
            cursor.execute(
                """
                INSERT INTO project_target_data_profiles (
                    project_id, connection_alias, dialect, transaction_policy, reviewed_by
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (project_id, connection_alias) DO UPDATE
                SET dialect = EXCLUDED.dialect,
                    transaction_policy = EXCLUDED.transaction_policy,
                    reviewed_by = EXCLUDED.reviewed_by,
                    reviewed_at = clock_timestamp(), updated_at = clock_timestamp()
                """,
                (project_id, connection_alias, dialect, transaction_policy, reviewed_by),
            )
            cursor.execute(
                "DELETE FROM project_target_data_query_bindings WHERE project_id = %s",
                (project_id,),
            )
            for value in bindings:
                cursor.execute(
                    """
                    INSERT INTO project_target_data_query_bindings (
                        project_id, connection_alias, query_binding_id, operation,
                        statement_text, target_schema, target_table, parameter_columns,
                        input_constraints, read_after_write_statement, read_assertion,
                        identity_contract, cleanup_binding_id, idempotency_policy, reviewed_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s
                    )
                    """,
                    (
                        project_id,
                        connection_alias,
                        value["query_binding_id"],
                        value["operation"],
                        value["statement_text"],
                        value["target_schema"],
                        value["target_table"],
                        _json(value["parameter_columns"]),
                        _json(value["input_constraints"]),
                        value["read_after_write_statement"],
                        _json(value["read_assertion"]),
                        _json(value["identity_contract"]),
                        value.get("cleanup_binding_id"),
                        value["idempotency_policy"],
                        reviewed_by,
                    ),
                )
            cursor.execute(
                """
                UPDATE project_workspaces
                SET target_data_connection_alias = %s,
                    updated_by = %s, updated_at = clock_timestamp()
                WHERE project_id = %s
                """,
                (connection_alias, reviewed_by, project_id),
            )
        profile = self.get(project_id)
        if profile is None:
            raise RuntimeError("Target Data Profile disappeared after replacement")
        return profile

    def get(self, project_id: str) -> TargetDataProfile | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT profile.project_id, profile.connection_alias, profile.dialect,
                       profile.transaction_policy, profile.reviewed_by, profile.reviewed_at
                FROM project_workspaces AS workspace
                JOIN project_target_data_profiles AS profile
                  ON profile.project_id = workspace.project_id
                 AND profile.connection_alias = workspace.target_data_connection_alias
                WHERE workspace.project_id = %s
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                SELECT project_id, connection_alias, query_binding_id, operation,
                       statement_text, target_schema, target_table, parameter_columns,
                       input_constraints, read_after_write_statement, read_assertion,
                       identity_contract, cleanup_binding_id, idempotency_policy
                FROM project_target_data_query_bindings
                WHERE project_id = %s AND connection_alias = %s
                ORDER BY query_binding_id
                """,
                (project_id, row["connection_alias"]),
            )
            bindings = tuple(
                _binding_from_row(value, dialect=str(row["dialect"]))
                for value in cursor.fetchall()
            )
        return TargetDataProfile(
            project_id=str(row["project_id"]),
            connection_alias=str(row["connection_alias"]),
            dialect=str(row["dialect"]),
            transaction_policy=str(row["transaction_policy"]),
            reviewed_by=str(row["reviewed_by"]),
            reviewed_at=row["reviewed_at"],
            bindings=bindings,
        )

    def binding(self, *, project_id: str, query_binding_id: str) -> TargetDataBinding:
        profile = self.get(project_id)
        if profile is None:
            raise ValueError("Project has no reviewed Target Data Profile")
        match = next(
            (value for value in profile.bindings if value.query_binding_id == query_binding_id),
            None,
        )
        if match is None:
            raise ValueError(f"SQL target has no reviewed query binding: {query_binding_id}")
        return match

    def validate_plan(self, *, project_id: str, plan: Mapping[str, object]) -> list[str]:
        flows = cast(list[dict[str, Any]], plan.get("generation_flows", []))
        sql_steps = [
            (flow, collection, step)
            for flow in flows
            for collection in ("steps", "cleanup_steps")
            for step in cast(list[dict[str, Any]], flow.get(collection, []))
            if step.get("channel") == "sql"
        ]
        if not sql_steps:
            return []
        profile = self.get(project_id)
        if profile is None:
            return ["Project has no reviewed Target Data Profile for SQL TestDataPlan execution"]
        bindings = {value.query_binding_id: value for value in profile.bindings}
        reasons: list[str] = []
        try:
            self._adapters.require(profile.dialect)
        except ValueError as error:
            reasons.append(str(error))
        for flow, collection, step in sql_steps:
            flow_id = str(flow.get("flow_id", "<unknown>"))
            step_id = str(step.get("step_id", "<unknown>"))
            target = str(step.get("target", ""))
            binding = bindings.get(target)
            if binding is None:
                reasons.append(f"{flow_id}/{step_id}: SQL query_binding_id is not reviewed")
                continue
            expected_operation = "cleanup" if collection == "cleanup_steps" else None
            if expected_operation and binding.operation != expected_operation:
                reasons.append(f"{flow_id}/{step_id}: cleanup must use a cleanup SQL binding")
            if collection == "steps" and step.get("data_effect") in {"creates", "updates"}:
                if binding.operation != "write":
                    reasons.append(f"{flow_id}/{step_id}: data generation must use a write binding")
                cleanup_targets = {
                    str(value.get("target", ""))
                    for value in cast(list[dict[str, Any]], flow.get("cleanup_steps", []))
                    if value.get("channel") == "sql"
                }
                if binding.cleanup_binding_id not in cleanup_targets:
                    reasons.append(
                        f"{flow_id}/{step_id}: reviewed cleanup binding "
                        f"{binding.cleanup_binding_id!r} is not present in cleanup_steps"
                    )
        reasons.extend(_validate_plan_identity_contracts(plan=plan, bindings=bindings))
        return sorted(set(reasons))


class ProjectSqlTestDataExecutor:
    """Execute only reviewed Project bindings against an owner-secret target DSN."""

    def __init__(
        self,
        *,
        control_database_url: str,
        evidence_store: LocalEvidenceStore,
        secret_store: TargetDataSecretStore | None = None,
        adapters: TargetDatabaseAdapterRegistry | None = None,
    ) -> None:
        self._control_database_url = control_database_url
        self._evidence_store = evidence_store
        self._adapters = adapters or default_target_database_adapters()
        self._secret_store = secret_store or TargetDataSecretStore(adapters=self._adapters)

    def execute(
        self,
        *,
        request: TestDataExecutionRequest,
        flow_id: str,
        step: Mapping[str, object],
        resolved_inputs: Mapping[str, object],
        variables: Mapping[str, object],
        phase: str,
    ) -> TestDataStepExecution:
        del variables
        binding_id = str(step.get("target", ""))
        with psycopg.connect(self._control_database_url) as control:
            binding = TargetDataProfileRepository(control).binding(
                project_id=request.project_id,
                query_binding_id=binding_id,
            )
        if (phase == "cleanup") != (binding.operation == "cleanup"):
            raise ValueError("SQL binding operation does not match the execution phase")
        forbidden = _RAW_SQL_INPUT_KEYS.intersection(resolved_inputs)
        if forbidden:
            raise ValueError(
                f"SQL Test data inputs contain forbidden raw SQL keys: {sorted(forbidden)}"
            )
        parameters = _validate_binding_inputs(binding, resolved_inputs)
        identity_source = step.get("_requires_unique_identity_match") is True
        adapter = self._adapters.require(binding.dialect)
        connection_dsn = self._secret_store.get(
            project_id=request.project_id,
            connection_alias=binding.connection_alias,
            dialect=binding.dialect,
        )
        try:
            database_result = adapter.execute_binding(
                connection_secret=connection_dsn,
                binding=binding,
                parameters=parameters,
            )
        except ValueError as error:
            if identity_source:
                raise TestDataStepBlockedError(
                    f"Identity SQL binding drifted: {binding.query_binding_id}: {error}"
                ) from error
            raise
        except TargetDatabaseExecutionError as error:
            raise RuntimeError(
                "Reviewed SQL binding failed: "
                f"{binding.query_binding_id} ({error.dialect}:{error.error_code})"
            ) from error
        rows = list(database_result.rows)
        if identity_source and len(rows) != 1:
            raise TestDataStepBlockedError(
                "Identity SQL readback must match exactly one database row: "
                f"count={len(rows)}"
            )
        _assert_readback(binding, rows)
        observed = {
            "query_binding_id": binding.query_binding_id,
            "database_dialect": binding.dialect,
            "affected_rows": database_result.affected_rows,
            "row_count": len(rows),
            "rows": [_json_safe(value) for value in rows],
            "read_after_write": "passed",
            "idempotency_policy": binding.idempotency_policy,
        }
        evidence_id = _evidence_id(request.run_id, flow_id, str(step["step_id"]), phase)
        stored = self._evidence_store.store_json(
            project_id=request.project_id,
            run_id=request.run_id,
            evidence_id=evidence_id,
            scenario_id=_safe_component(flow_id),
            evidence_type="sql",
            payload=redact_secret_evidence(observed),
        )
        evidence = TestDataExecutionEvidence(
            evidence_id=evidence_id,
            flow_id=flow_id,
            step_id=str(step["step_id"]),
            phase=phase,
            evidence_type="sql",
            evidence_ref=stored.evidence_ref,
            content_digest=stored.content_digest,
            sanitized=True,
        )
        return TestDataStepExecution(
            source_values={"database": observed}, evidence=(evidence,)
        )


def _validate_profile_input(
    *,
    project_id: str,
    connection_alias: str,
    dialect: str = "postgresql",
    transaction_policy: str,
    bindings: Sequence[Mapping[str, object]],
    reviewed_by: str,
    adapters: TargetDatabaseAdapterRegistry | None = None,
) -> None:
    if not project_id.strip() or not connection_alias.strip() or not reviewed_by.strip():
        raise ValueError("Target Data Profile identity must not be blank")
    if transaction_policy != "per_binding_transaction":
        raise ValueError("Target Data transaction_policy must be per_binding_transaction")
    adapter = (adapters or default_target_database_adapters()).require(dialect)
    if not bindings:
        raise ValueError("Target Data Profile requires at least one reviewed query binding")
    ids = [str(value.get("query_binding_id", "")) for value in bindings]
    if any(not value or _SAFE_IDENTIFIER.fullmatch(value) is None for value in ids):
        raise ValueError("query_binding_id must be a safe named identifier")
    if len(ids) != len(set(ids)):
        raise ValueError("query_binding_id values must be unique within a Project")
    by_id = {str(value["query_binding_id"]): value for value in bindings}
    for value in bindings:
        binding_id = str(value["query_binding_id"])
        operation = str(value.get("operation", ""))
        if operation not in {"write", "read", "cleanup"}:
            raise ValueError(f"{binding_id}: SQL binding operation is invalid")
        for key in ("target_schema", "target_table"):
            if _SAFE_IDENTIFIER.fullmatch(str(value.get(key, ""))) is None:
                raise ValueError(f"{binding_id}: {key} must be a safe database identifier")
        statements = (
            str(value.get("statement_text", "")),
            str(value.get("read_after_write_statement", "")),
        )
        if any(
            not statement.strip() or ";" in statement.rstrip().rstrip(";")
            for statement in statements
        ):
            raise ValueError(f"{binding_id}: each SQL binding must contain one statement")
        parameter_columns = value.get("parameter_columns")
        constraints = value.get("input_constraints")
        if not isinstance(parameter_columns, dict) or not isinstance(constraints, dict):
            raise ValueError(f"{binding_id}: parameter_columns/input_constraints must be objects")
        adapter.validate_binding_definition(value)
        for parameter, column in parameter_columns.items():
            if _SAFE_IDENTIFIER.fullmatch(str(column)) is None:
                raise ValueError(f"{binding_id}: column for {parameter} is invalid")
            _validate_constraint_definition(binding_id, str(parameter), constraints[parameter])
        assertion = value.get("read_assertion")
        if not isinstance(assertion, dict) or assertion.get("mode") not in {
            "rows_present",
            "rows_absent",
            "row_count",
        }:
            raise ValueError(f"{binding_id}: read_assertion is invalid")
        if assertion.get("mode") == "row_count" and not isinstance(assertion.get("count"), int):
            raise ValueError(f"{binding_id}: row_count assertion requires an integer count")
        if operation == "cleanup" and not (
            assertion.get("mode") == "rows_absent"
            or (
                assertion.get("mode") == "row_count"
                and assertion.get("count") == 0
            )
        ):
            raise ValueError(
                f"{binding_id}: cleanup binding must prove that the target row is absent"
            )
        _validate_identity_contract(binding_id, value.get("identity_contract"))
        if operation == "cleanup":
            identity_contract = cast(dict[str, object], value["identity_contract"])
            parameter_targets = {str(column) for column in parameter_columns.values()}
            primary_key = str(identity_contract["primary_key"])
            business_keys = {
                str(column)
                for column in cast(list[object], identity_contract["business_unique_keys"])
            }
            if primary_key not in parameter_targets and not business_keys.issubset(
                parameter_targets
            ):
                raise ValueError(
                    f"{binding_id}: cleanup binding must target the primary key or every "
                    "business unique key"
                )
        policy = str(value.get("idempotency_policy", ""))
        if policy not in _IDEMPOTENCY_POLICIES:
            raise ValueError(f"{binding_id}: idempotency_policy is invalid")
        cleanup_id = value.get("cleanup_binding_id")
        if operation == "write":
            cleanup = by_id.get(str(cleanup_id or ""))
            if cleanup is None or cleanup.get("operation") != "cleanup":
                raise ValueError(f"{binding_id}: write binding requires a reviewed cleanup binding")
            if policy == "read_only":
                raise ValueError(f"{binding_id}: write binding cannot be read_only")
        elif cleanup_id is not None:
            raise ValueError(f"{binding_id}: only write bindings may reference cleanup_binding_id")
        elif operation == "read" and policy != "read_only":
            raise ValueError(f"{binding_id}: read binding must use read_only idempotency")


def _validate_constraint_definition(binding_id: str, parameter: str, value: object) -> None:
    if not isinstance(value, dict) or value.get("type") not in _INPUT_TYPES:
        raise ValueError(f"{binding_id}/{parameter}: input constraint type is invalid")
    if value.get("required") is not True:
        raise ValueError(f"{binding_id}/{parameter}: every SQL parameter must be required")
    if value["type"] == "string" and not isinstance(value.get("max_length"), int):
        raise ValueError(f"{binding_id}/{parameter}: string constraint requires max_length")
    enum = value.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"{binding_id}/{parameter}: enum must be a non-empty array")


def _validate_identity_contract(binding_id: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "primary_key",
        "business_unique_keys",
        "screen_key",
        "coverage_columns",
    }:
        raise ValueError(f"{binding_id}: identity_contract is incomplete")
    primary = value.get("primary_key")
    screen = value.get("screen_key")
    business = value.get("business_unique_keys")
    coverage = value.get("coverage_columns")
    identifiers = [
        primary,
        screen,
        *(business if isinstance(business, list) else []),
        *(coverage if isinstance(coverage, list) else []),
    ]
    if (
        not isinstance(primary, str)
        or not isinstance(screen, str)
        or not isinstance(business, list)
        or not business
        or not isinstance(coverage, list)
        or not coverage
        or any(
            not isinstance(item, str) or _SAFE_IDENTIFIER.fullmatch(item) is None
            for item in identifiers
        )
        or len(business) != len(set(business))
        or len(coverage) != len(set(coverage))
        or any(is_sensitive_data_identity_name(str(item)) for item in identifiers)
    ):
        raise ValueError(f"{binding_id}: identity_contract columns are invalid")


def _validate_plan_identity_contracts(
    *,
    plan: Mapping[str, object],
    bindings: Mapping[str, TargetDataBinding],
) -> list[str]:
    flows = cast(list[dict[str, Any]], plan.get("generation_flows", []))
    flow_by_id = {str(flow.get("flow_id", "")): flow for flow in flows}
    reasons: list[str] = []
    for data_set in cast(list[dict[str, Any]], plan.get("data_sets", [])):
        test_data_id = str(data_set.get("test_data_id", "<unknown>"))
        identity = data_set.get("identity_binding")
        if not isinstance(identity, dict):
            continue
        flow = flow_by_id.get(str(identity.get("source_flow_id", "")))
        steps = cast(list[dict[str, Any]], (flow or {}).get("steps", []))
        source_index = next(
            (
                index
                for index, value in enumerate(steps)
                if value.get("step_id") == identity.get("source_step_id")
            ),
            None,
        )
        provider = cast(dict[str, object], identity.get("provider") or {})
        provider_type = str(provider.get("type", ""))
        identity_binding: TargetDataBinding | None = None
        if source_index is not None and provider_type == "database":
            identity_binding = bindings.get(str(steps[source_index].get("target", "")))
        elif source_index is not None and provider_type == "hybrid":
            sql_steps = [
                step
                for step in steps[: source_index + 1]
                if step.get("channel") == "sql"
            ]
            if sql_steps:
                identity_binding = bindings.get(str(sql_steps[-1].get("target", "")))
            elif _identity_uses_database(identity):
                reasons.append(
                    f"{test_data_id}: hybrid database identity source has no SQL step"
                )
        if identity_binding is not None:
            reasons.extend(
                _validate_database_identity_specs(
                    test_data_id=test_data_id,
                    identity=identity,
                    binding=identity_binding,
                    provider_type=provider_type,
                )
            )
        for condition in cast(
            list[dict[str, Any]], data_set.get("coverage_conditions", [])
        ):
            coverage_flow = flow_by_id.get(str(condition.get("source_flow_id", "")))
            coverage_step = next(
                (
                    value
                    for value in cast(
                        list[dict[str, Any]], (coverage_flow or {}).get("steps", [])
                    )
                    if value.get("step_id") == condition.get("source_step_id")
                ),
                None,
            )
            coverage_binding = bindings.get(
                str((coverage_step or {}).get("target", ""))
            )
            if coverage_binding is None:
                continue
            allowed_coverage_columns = {
                str(value)
                for value in cast(
                    list[object],
                    coverage_binding.identity_contract["coverage_columns"],
                )
            }
            for key in ("path", "expected_path"):
                if key not in condition:
                    continue
                column = _coverage_column(str(condition[key]))
                if column not in allowed_coverage_columns:
                    reasons.append(
                        f"{test_data_id}: coverage condition column is not reviewed by "
                        f"{coverage_binding.query_binding_id}: {column}"
                    )
    return reasons


def _identity_uses_database(identity: Mapping[str, object]) -> bool:
    return any(
        value.get("source") == "database"
        for value in (
            cast(Mapping[str, object], identity.get("primary_key") or {}),
            *cast(
                list[Mapping[str, object]],
                identity.get("business_unique_keys") or [],
            ),
            cast(Mapping[str, object], identity.get("screen_key") or {}),
            cast(Mapping[str, object], identity.get("match_count") or {}),
        )
    )


def _validate_database_identity_specs(
    *,
    test_data_id: str,
    identity: Mapping[str, object],
    binding: TargetDataBinding,
    provider_type: str,
) -> list[str]:
    contract = binding.identity_contract
    primary = cast(Mapping[str, object], identity.get("primary_key") or {})
    business = cast(
        list[Mapping[str, object]], identity.get("business_unique_keys") or []
    )
    screen = cast(Mapping[str, object], identity.get("screen_key") or {})
    reviewed_business = [
        str(value) for value in cast(list[object], contract["business_unique_keys"])
    ]
    reasons: list[str] = []
    if provider_type == "database" and [str(value.get("name", "")) for value in business] != (
        reviewed_business
    ):
        reasons.append(
            f"{test_data_id}: planned identity keys differ from reviewed SQL binding"
        )
    specifications = [
        (primary, {str(contract["primary_key"])}),
        *((value, set(reviewed_business)) for value in business),
        (screen, {str(contract["screen_key"])}),
    ]
    for spec, allowed_columns in specifications:
        if provider_type == "hybrid" and spec.get("source") != "database":
            continue
        column = str(spec.get("name", ""))
        if (
            spec.get("source") != "database"
            or column not in allowed_columns
            or str(spec.get("path", ""))
            not in {f"rows[0].{column}", f"$.rows[0].{column}"}
        ):
            expected = ", ".join(sorted(allowed_columns))
            reasons.append(
                f"{test_data_id}: {expected} must bind the reviewed database "
                "readback column"
            )
    if binding.read_assertion != {"mode": "row_count", "count": 1}:
        reasons.append(
            f"{test_data_id}: identity SQL binding must assert readback row_count=1"
        )
    return reasons


def _coverage_column(path: str) -> str:
    normalized = path[2:] if path.startswith("$.") else path
    prefix = "rows[0]."
    if not normalized.startswith(prefix):
        return ""
    return normalized[len(prefix) :].split(".", 1)[0]


def _validate_binding_inputs(
    binding: TargetDataBinding, inputs: Mapping[str, object]
) -> dict[str, object]:
    expected = set(binding.input_constraints)
    if set(inputs) != expected:
        raise ValueError(
            f"{binding.query_binding_id}: inputs must match reviewed parameters exactly; "
            f"expected={sorted(expected)}"
        )
    result: dict[str, object] = {}
    for name, constraint in binding.input_constraints.items():
        value = inputs[name]
        kind = constraint["type"]
        valid = (
            (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "number" and isinstance(value, int | float) and not isinstance(value, bool))
            or (kind == "boolean" and isinstance(value, bool))
        )
        if not valid:
            raise ValueError(f"{binding.query_binding_id}/{name}: input type is invalid")
        if isinstance(value, str):
            if len(value) > cast(int, constraint["max_length"]):
                raise ValueError(f"{binding.query_binding_id}/{name}: input exceeds max_length")
            pattern = constraint.get("pattern")
            if pattern is not None and re.fullmatch(str(pattern), value) is None:
                raise ValueError(f"{binding.query_binding_id}/{name}: input violates pattern")
        if "enum" in constraint and value not in cast(list[object], constraint["enum"]):
            raise ValueError(f"{binding.query_binding_id}/{name}: input is outside reviewed enum")
        if "minimum" in constraint and cast(int | float, value) < cast(
            int | float, constraint["minimum"]
        ):
            raise ValueError(f"{binding.query_binding_id}/{name}: input is below minimum")
        if "maximum" in constraint and cast(int | float, value) > cast(
            int | float, constraint["maximum"]
        ):
            raise ValueError(f"{binding.query_binding_id}/{name}: input exceeds maximum")
        result[name] = value
    return result


def _assert_readback(binding: TargetDataBinding, rows: Sequence[Mapping[str, object]]) -> None:
    mode = binding.read_assertion["mode"]
    if mode == "rows_present" and not rows:
        raise ValueError(f"{binding.query_binding_id}: read-after-write found no rows")
    if mode == "rows_absent" and rows:
        raise ValueError(f"{binding.query_binding_id}: cleanup readback still found rows")
    if mode == "row_count" and len(rows) != cast(int, binding.read_assertion["count"]):
        raise ValueError(f"{binding.query_binding_id}: readback row count did not match")


def _binding_from_row(row: Mapping[str, object], *, dialect: str) -> TargetDataBinding:
    return TargetDataBinding(
        project_id=str(row["project_id"]),
        connection_alias=str(row["connection_alias"]),
        dialect=dialect,
        query_binding_id=str(row["query_binding_id"]),
        operation=str(row["operation"]),
        statement_text=str(row["statement_text"]),
        target_schema=str(row["target_schema"]),
        target_table=str(row["target_table"]),
        parameter_columns=cast(dict[str, str], row["parameter_columns"]),
        input_constraints=cast(dict[str, dict[str, object]], row["input_constraints"]),
        read_after_write_statement=str(row["read_after_write_statement"]),
        read_assertion=cast(dict[str, object], row["read_assertion"]),
        identity_contract=cast(dict[str, object], row["identity_contract"]),
        cleanup_binding_id=(
            str(row["cleanup_binding_id"]) if row["cleanup_binding_id"] is not None else None
        ),
        idempotency_policy=str(row["idempotency_policy"]),
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return cleaned[:120] or "flow"


def _evidence_id(run_id: str, flow_id: str, step_id: str, phase: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{flow_id}\0{step_id}\0{phase}\0sql".encode()).hexdigest()
    return f"test-data-sql-{digest[:32]}"
