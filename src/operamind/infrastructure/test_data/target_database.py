"""Pluggable database adapters for a Project's tested system."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

_DIALECT_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_POSTGRESQL_NAMED_PARAMETER = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")


class TargetDatabaseBinding(Protocol):
    """Driver-neutral view of one reviewed Target Data query binding."""

    @property
    def query_binding_id(self) -> str: ...

    @property
    def statement_text(self) -> str: ...

    @property
    def read_after_write_statement(self) -> str: ...

    @property
    def target_schema(self) -> str: ...

    @property
    def target_table(self) -> str: ...

    @property
    def parameter_columns(self) -> Mapping[str, str]: ...

    @property
    def input_constraints(self) -> Mapping[str, Mapping[str, object]]: ...

    @property
    def identity_contract(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class TargetDatabaseExecutionResult:
    """Normalized result returned by every tested-system database adapter."""

    affected_rows: int
    rows: tuple[Mapping[str, object], ...]


class TargetDatabaseExecutionError(RuntimeError):
    """Sanitized target database failure without a connection secret or SQL text."""

    def __init__(self, *, dialect: str, error_code: str) -> None:
        super().__init__(f"{dialect} target database execution failed ({error_code})")
        self.dialect = dialect
        self.error_code = error_code


class TargetDatabaseAdapter(Protocol):
    """Extension point for one tested-system database dialect."""

    dialect: str

    def validate_connection_secret(self, value: str) -> None: ...

    def validate_binding_definition(self, value: Mapping[str, object]) -> None: ...

    def execute_binding(
        self,
        *,
        connection_secret: str,
        binding: TargetDatabaseBinding,
        parameters: Mapping[str, object],
    ) -> TargetDatabaseExecutionResult: ...


class TargetDatabaseAdapterRegistry:
    """Explicit dialect registry; unsupported dialects always fail closed."""

    def __init__(self, adapters: Sequence[TargetDatabaseAdapter]) -> None:
        registered: dict[str, TargetDatabaseAdapter] = {}
        for adapter in adapters:
            dialect = adapter.dialect.strip()
            if _DIALECT_ID.fullmatch(dialect) is None:
                raise ValueError("Target Database Adapter dialect is invalid")
            if dialect in registered:
                raise ValueError(f"Duplicate Target Database Adapter dialect: {dialect}")
            registered[dialect] = adapter
        if not registered:
            raise ValueError("Target Database Adapter Registry must not be empty")
        self._adapters = registered

    @property
    def dialects(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def require(self, dialect: str) -> TargetDatabaseAdapter:
        adapter = self._adapters.get(dialect)
        if adapter is None:
            raise ValueError(f"Target database dialect has no registered Adapter: {dialect}")
        return adapter


class PostgresqlTargetDatabaseAdapter:
    """Current production adapter for a PostgreSQL tested-system database."""

    dialect = "postgresql"

    def validate_connection_secret(self, value: str) -> None:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path
        ):
            raise ValueError("Target Data connection must be a PostgreSQL URL")
        if parsed.password is None:
            raise ValueError("Target Data connection secret must include a password")

    def validate_binding_definition(self, value: Mapping[str, object]) -> None:
        statements = (
            str(value.get("statement_text", "")),
            str(value.get("read_after_write_statement", "")),
        )
        parameters = set().union(
            *(_POSTGRESQL_NAMED_PARAMETER.findall(statement) for statement in statements)
        )
        parameter_columns = value.get("parameter_columns")
        constraints = value.get("input_constraints")
        if not isinstance(parameter_columns, dict) or not isinstance(constraints, dict):
            return
        if parameters != set(parameter_columns) or parameters != set(constraints):
            binding_id = str(value.get("query_binding_id", "<unknown>"))
            raise ValueError(
                f"{binding_id}: PostgreSQL named parameters, columns and constraints "
                "must match exactly"
            )

    def execute_binding(
        self,
        *,
        connection_secret: str,
        binding: TargetDatabaseBinding,
        parameters: Mapping[str, object],
    ) -> TargetDatabaseExecutionResult:
        self.validate_connection_secret(connection_secret)
        try:
            with (
                psycopg.connect(connection_secret, row_factory=dict_row) as connection,
                connection.cursor() as cursor,
            ):
                _validate_postgresql_live_columns(cursor, binding, parameters)
                _validate_postgresql_live_identity_constraints(cursor, binding)
                cursor.execute(binding.statement_text, parameters)
                affected_rows = max(cursor.rowcount, 0)
                cursor.execute(binding.read_after_write_statement, parameters)
                rows = tuple(dict(value) for value in cursor.fetchall())
        except psycopg.Error as error:
            raise TargetDatabaseExecutionError(
                dialect=self.dialect,
                error_code=error.sqlstate or "db_error",
            ) from error
        return TargetDatabaseExecutionResult(
            affected_rows=affected_rows,
            rows=rows,
        )


def default_target_database_adapters() -> TargetDatabaseAdapterRegistry:
    """Return only adapters with a real production implementation."""

    return TargetDatabaseAdapterRegistry((PostgresqlTargetDatabaseAdapter(),))


def _validate_postgresql_live_columns(
    cursor: Any,
    binding: TargetDatabaseBinding,
    parameters: Mapping[str, object],
) -> None:
    cursor.execute(
        """
        SELECT column_name, is_nullable, data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (binding.target_schema, binding.target_table),
    )
    columns = {str(row["column_name"]): row for row in cursor.fetchall()}
    for parameter, column_name in binding.parameter_columns.items():
        column = columns.get(column_name)
        if column is None:
            raise ValueError(
                f"{binding.query_binding_id}: reviewed target column no longer exists: "
                f"{column_name}"
            )
        constraint = binding.input_constraints[parameter]
        data_type = str(column["data_type"])
        if not _postgresql_column_accepts_input_type(str(constraint["type"]), data_type):
            raise ValueError(
                f"{binding.query_binding_id}/{parameter}: reviewed input type is incompatible "
                f"with target column type {data_type}"
            )
        if constraint.get("required") is True and parameters[parameter] is None:
            raise ValueError(f"{binding.query_binding_id}/{parameter}: NULL is not allowed")
        maximum = column["character_maximum_length"]
        if (
            isinstance(parameters[parameter], str)
            and maximum is not None
            and len(cast(str, parameters[parameter])) > int(maximum)
        ):
            raise ValueError(
                f"{binding.query_binding_id}/{parameter}: input exceeds target column length"
            )


def _validate_postgresql_live_identity_constraints(
    cursor: Any, binding: TargetDatabaseBinding
) -> None:
    contract = binding.identity_contract
    primary = str(contract["primary_key"])
    business = tuple(
        str(value) for value in cast(list[object], contract["business_unique_keys"])
    )
    coverage = tuple(str(value) for value in cast(list[object], contract["coverage_columns"]))
    screen = str(contract["screen_key"])
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        (binding.target_schema, binding.target_table),
    )
    columns = {str(row["column_name"]) for row in cursor.fetchall()}
    missing = {primary, screen, *business, *coverage} - columns
    if missing:
        raise ValueError(
            f"{binding.query_binding_id}: reviewed identity columns no longer exist: "
            f"{sorted(missing)}"
        )
    cursor.execute(
        """
        SELECT constraint_record.contype AS constraint_type,
               constraint_record.conname AS constraint_name,
               column_record.attname AS column_name,
               key_column.ordinality AS ordinal_position
        FROM pg_catalog.pg_constraint AS constraint_record
        JOIN pg_catalog.pg_class AS table_record
          ON table_record.oid = constraint_record.conrelid
        JOIN pg_catalog.pg_namespace AS namespace_record
          ON namespace_record.oid = table_record.relnamespace
        JOIN unnest(constraint_record.conkey) WITH ORDINALITY
          AS key_column(attribute_number, ordinality)
          ON TRUE
        JOIN pg_catalog.pg_attribute AS column_record
          ON column_record.attrelid = table_record.oid
         AND column_record.attnum = key_column.attribute_number
        WHERE namespace_record.nspname = %s
          AND table_record.relname = %s
          AND constraint_record.contype IN ('p', 'u')
        ORDER BY constraint_record.contype,
                 constraint_record.conname, key_column.ordinality
        """,
        (binding.target_schema, binding.target_table),
    )
    constraints: dict[tuple[str, str], list[str]] = {}
    for row in cursor.fetchall():
        constraints.setdefault(
            (str(row["constraint_type"]), str(row["constraint_name"])), []
        ).append(str(row["column_name"]))
    primary_constraints = {
        tuple(columns)
        for (kind, _name), columns in constraints.items()
        if kind == "p"
    }
    unique_constraints = {
        tuple(columns)
        for (kind, _name), columns in constraints.items()
        if kind == "u"
    }
    if (primary,) not in primary_constraints:
        raise ValueError(
            f"{binding.query_binding_id}: reviewed primary key is not the live single-column PK"
        )
    if business not in unique_constraints:
        raise ValueError(
            f"{binding.query_binding_id}: reviewed business key is not a live UNIQUE constraint"
        )


def _postgresql_column_accepts_input_type(input_type: str, data_type: str) -> bool:
    database_type = data_type.lower()
    compatible = {
        "string": {
            "character varying",
            "character",
            "text",
            "uuid",
            "date",
            "timestamp without time zone",
            "timestamp with time zone",
        },
        "integer": {"smallint", "integer", "bigint"},
        "number": {
            "smallint",
            "integer",
            "bigint",
            "numeric",
            "decimal",
            "real",
            "double precision",
        },
        "boolean": {"boolean"},
    }
    return database_type in compatible.get(input_type, set())
