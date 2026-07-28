"""SDK-free MCP 2025-11-25 stdio server for bounded Copilot tools."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO, cast

import psycopg
from jsonschema import Draft202012Validator
from psycopg import Connection

from operamind.application import (
    ChangedLineCoverageEvidence,
    CopilotCodingTaskService,
)
from operamind.contracts import ContractCatalog

MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "operamind-vnext"
SERVER_VERSION = "0.1.0.dev0"
MCP_TOOL_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")


def _string() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


def _schema(properties: Mapping[str, object], required: tuple[str, ...]) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


def _changed_line_coverage_schema() -> dict[str, object]:
    line_map = {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "uniqueItems": True,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": _string(),
            },
            "executable_lines": line_map,
            "covered_lines": line_map,
            "minimum_coverage_percent": {
                "type": "number",
                "minimum": 80,
                "maximum": 100,
            },
        },
        "required": [
            "evidence_refs",
            "executable_lines",
            "covered_lines",
            "minimum_coverage_percent",
        ],
    }


def _change_outputs_schema() -> dict[str, object]:
    schema = _schema(
        {
            "coding_task_id": _string(),
            "workspace_root": _string(),
            "output_stage": {
                "enum": ["document_change", "code_scope", "test_planning"]
            },
            "document_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": _string(),
            },
            "code_scope": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "target_path",
                        "target_symbols",
                        "recommended_action",
                        "test_file_refs",
                        "rationale",
                        "ui_impact",
                    ],
                    "properties": {
                        "target_path": _string(),
                        "target_symbols": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": _string(),
                        },
                        "recommended_action": {
                            "enum": ["modify", "add", "delete", "review_only"]
                        },
                        "test_file_refs": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": _string(),
                        },
                        "rationale": _string(),
                        "ui_impact": {"type": "boolean"},
                    },
                },
            },
            "test_plan": {"type": "object"},
            "test_data_plan": {"type": "object"},
        },
        ("coding_task_id", "workspace_root", "output_stage"),
    )
    schema["oneOf"] = [
        {
            "properties": {"output_stage": {"const": "document_change"}},
            "required": ["document_ids"],
        },
        {
            "properties": {"output_stage": {"const": "code_scope"}},
            "required": ["code_scope"],
        },
        {
            "properties": {"output_stage": {"const": "test_planning"}},
            "required": ["test_plan", "test_data_plan"],
        },
    ]
    return schema


TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "copilot_get_coding_task",
        "title": "Load one unified Copilot Change Task",
        "description": (
            "Load the current ordered stage of one Change Task after the VS Code user "
            "confirms the local Bridge notification."
        ),
        "inputSchema": _schema(
            {"coding_task_id": _string(), "workspace_root": _string()},
            ("coding_task_id", "workspace_root"),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_run_task_command",
        "title": "Run a test command bound to one Coding Task",
        "description": (
            "Run one task-bound command and automatically publish its digest-only "
            "result to the Coding Task timeline used by OperaMind Web."
        ),
        "inputSchema": _schema(
            {
                "coding_task_id": _string(),
                "workspace_root": _string(),
                "command_execution_id": _string(),
                "command_ref": _string(),
            },
            (
                "coding_task_id",
                "workspace_root",
                "command_execution_id",
                "command_ref",
            ),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_record_change_outputs",
        "title": "Record one ordered Change Task output stage",
        "description": (
            "Record exactly one ordered stage: materialize a Canonical design diff, validate "
            "a Code Graph scope, or validate TestPlan/TestDataPlan after the code diff."
        ),
        "inputSchema": _change_outputs_schema(),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_validate_task_diff",
        "title": "Validate and publish the current Coding Task diff",
        "description": (
            "Compare all current Git path changes with the Change Task path allowlist and "
            "automatically publish the path-only Diff result to OperaMind Web."
        ),
        "inputSchema": _schema(
            {
                "coding_task_id": _string(),
                "workspace_root": _string(),
                "edit_result_id": _string(),
            },
            ("coding_task_id", "workspace_root", "edit_result_id"),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_record_task_result",
        "title": "Record the committed Coding Task result",
        "description": (
            "Validate the committed Diff, bind the Task command evidence, and publish the "
            "final result to OperaMind Web without a response file. Source changes require "
            "changed_line_coverage evidence before Closure can pass."
        ),
        "inputSchema": _schema(
            {
                "coding_task_id": _string(),
                "workspace_root": _string(),
                "edit_result_id": _string(),
                "test_result_refs": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": _string(),
                },
                "tests_passed": {"type": "boolean"},
                "changed_line_coverage": _changed_line_coverage_schema(),
            },
            (
                "coding_task_id",
                "workspace_root",
                "edit_result_id",
                "test_result_refs",
                "tests_passed",
            ),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
)
if invalid_tool_names := [
    str(tool["name"])
    for tool in TOOLS
    if MCP_TOOL_NAME_PATTERN.fullmatch(str(tool["name"])) is None
]:
    raise RuntimeError(
        "MCP tool names are incompatible with VS Code: " + ", ".join(invalid_tool_names)
    )
TOOL_BY_NAME = {str(tool["name"]): tool for tool in TOOLS}


class UnknownToolError(ValueError):
    """Raised when tools/call names a tool not exposed by this server."""


class CopilotToolDispatcher:
    """Map validated MCP tool arguments to existing application use cases."""

    def __init__(self, *, connection: Connection[Any], root: Path) -> None:
        self._connection = connection
        self._contracts = ContractCatalog.load(root.resolve() / "contracts")

    def call(self, name: str, arguments: object) -> dict[str, object]:
        tool = TOOL_BY_NAME.get(name)
        if tool is None:
            raise UnknownToolError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        schema = cast(dict[str, Any], tool["inputSchema"])
        errors = sorted(
            Draft202012Validator(schema).iter_errors(arguments),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            error = errors[0]
            location = "/".join(str(part) for part in error.absolute_path) or "$"
            raise ValueError(f"Invalid tool arguments at {location}: {error.message}")
        args = cast(dict[str, object], arguments)
        with self._connection.transaction():
            return self._dispatch(name, args)

    def _dispatch(self, name: str, args: dict[str, object]) -> dict[str, object]:
        coding_tasks = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._contracts.root.parent,
        )
        if name == "copilot_get_coding_task":
            return coding_tasks.get_mcp_context(
                coding_task_id=_text(args, "coding_task_id"),
                workspace_root=Path(_text(args, "workspace_root")),
            )
        if name == "copilot_run_task_command":
            return _public_command_result(
                coding_tasks.run_command(
                    coding_task_id=_text(args, "coding_task_id"),
                    command_execution_id=_text(args, "command_execution_id"),
                    command_ref=_text(args, "command_ref"),
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            )
        if name == "copilot_record_change_outputs":
            output_stage = _text(args, "output_stage")
            internal_result = coding_tasks.record_change_outputs(
                coding_task_id=_text(args, "coding_task_id"),
                workspace_root=Path(_text(args, "workspace_root")),
                output_stage=output_stage,
                document_ids=tuple(
                    str(value)
                    for value in cast(list[object], args.get("document_ids", []))
                ),
                code_scope=tuple(
                    cast(dict[str, Any], value)
                    for value in cast(list[object], args.get("code_scope", []))
                ),
                test_plan=cast(dict[str, Any], args["test_plan"])
                if "test_plan" in args
                else None,
                test_data_plan=cast(dict[str, Any], args["test_data_plan"])
                if "test_data_plan" in args
                else None,
            )
            result = _public_change_output(internal_result, output_stage=output_stage)
            from operamind.application.web_control_plane import WebControlPlaneService

            task_view = coding_tasks.view(_text(args, "coding_task_id"))
            task_artifact = cast(dict[str, object], task_view["task"])
            service = WebControlPlaneService(
                connection=self._connection,
                repository_root=self._contracts.root.parent,
            )
            automation = service.resume_pending_change_automation(
                request_id=str(task_artifact["change_request_id"]),
                actor="mcp:github-copilot",
            )
            result["flow_status"] = _public_flow_status(
                service.main_change_flow(str(task_artifact["change_request_id"]))
            )
            if not isinstance(automation, dict) or automation.get("status") != "blocked":
                result["next_context"] = coding_tasks.get_mcp_context(
                    coding_task_id=_text(args, "coding_task_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            else:
                result["next_context"] = None
            return result
        if name == "copilot_validate_task_diff":
            return _public_edit_result(
                coding_tasks.validate_diff(
                    coding_task_id=_text(args, "coding_task_id"),
                    edit_result_id=_text(args, "edit_result_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            )
        if name == "copilot_record_task_result":
            result = _public_edit_result(
                coding_tasks.record_result(
                    coding_task_id=_text(args, "coding_task_id"),
                    edit_result_id=_text(args, "edit_result_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                    test_result_refs=tuple(
                        str(value)
                        for value in cast(list[object], args["test_result_refs"])
                    ),
                    tests_passed=cast(bool, args["tests_passed"]),
                    changed_line_coverage=_changed_line_coverage(args),
                )
            )
            from operamind.application.web_control_plane import WebControlPlaneService

            task_view = coding_tasks.view(_text(args, "coding_task_id"))
            task_artifact = cast(dict[str, object], task_view["task"])
            service = WebControlPlaneService(
                connection=self._connection,
                repository_root=self._contracts.root.parent,
            )
            service.resume_pending_change_automation(
                request_id=str(task_artifact["change_request_id"]),
                actor="mcp:github-copilot",
            )
            result["flow_status"] = _public_flow_status(
                service.main_change_flow(str(task_artifact["change_request_id"]))
            )
            return result
        raise AssertionError(f"Tool dispatch is incomplete: {name}")


class McpProtocolError(ValueError):
    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class OperaMindMcpServer:
    """Stateful newline-delimited JSON-RPC handler for one stdio client session."""

    def __init__(self, dispatcher: CopilotToolDispatcher, *, max_tool_calls: int = 100) -> None:
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        self._dispatcher = dispatcher
        self._max_tool_calls = max_tool_calls
        self._tool_calls = 0
        self._initialize_responded = False
        self._ready = False

    def handle(self, message: object) -> dict[str, object] | None:
        request_id: object = None
        try:
            if not isinstance(message, dict):
                raise McpProtocolError(-32600, "Invalid Request")
            request_id = message.get("id")
            if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                raise McpProtocolError(-32600, "Invalid Request")
            if "id" in message and not _valid_request_id(request_id):
                raise McpProtocolError(-32600, "Invalid Request ID")
            method = str(message["method"])
            params = message.get("params", {})
            if method == "initialize":
                return self._initialize(request_id, params)
            if method == "ping":
                _require_request_id(message)
                return _success(request_id, {})
            if method == "notifications/initialized":
                if "id" in message or not self._initialize_responded:
                    raise McpProtocolError(-32600, "Invalid initialized notification")
                self._ready = True
                return None
            if method == "notifications/cancelled":
                return None
            if not self._ready:
                raise McpProtocolError(-32002, "Server is not initialized")
            if method == "tools/list":
                _require_request_id(message)
                if not isinstance(params, dict) or params.get("cursor") is not None:
                    raise McpProtocolError(-32602, "Pagination cursor is not supported")
                return _success(request_id, {"tools": list(TOOLS)})
            if method == "tools/call":
                _require_request_id(message)
                return self._call_tool(request_id, params)
            if "id" not in message:
                return None
            raise McpProtocolError(-32601, f"Method not found: {method}")
        except McpProtocolError as error:
            return _error(request_id, error.code, str(error), error.data)

    def serve(self, input_stream: TextIO, output_stream: TextIO) -> None:
        for line in input_stream:
            response: dict[str, object] | None
            try:
                message: object = json.loads(line)
            except json.JSONDecodeError:
                response = _error(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                output_stream.write(_json(response) + "\n")
                output_stream.flush()

    def _initialize(self, request_id: object, params: object) -> dict[str, object]:
        if self._initialize_responded:
            raise McpProtocolError(-32600, "Server is already initialized")
        if request_id is None or not isinstance(params, dict):
            raise McpProtocolError(-32602, "initialize requires request params")
        if not isinstance(params.get("protocolVersion"), str):
            raise McpProtocolError(-32602, "initialize requires protocolVersion")
        if not isinstance(params.get("capabilities"), dict) or not isinstance(
            params.get("clientInfo"), dict
        ):
            raise McpProtocolError(-32602, "initialize requires capabilities and clientInfo")
        self._initialize_responded = True
        return _success(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "title": "OperaMind vNext",
                    "version": SERVER_VERSION,
                    "description": "Bounded local Copilot unified Change Task",
                },
                "instructions": (
                    "Follow the current Change Task stage in order. Use only Canonical RAG "
                    "documents and, once bound, only the validated execution scope. Never submit "
                    "arbitrary shell commands or out-of-scope files."
                ),
            },
        )

    def _call_tool(self, request_id: object, params: object) -> dict[str, object]:
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            raise McpProtocolError(-32602, "tools/call requires a tool name")
        name = str(params["name"])
        if name not in TOOL_BY_NAME:
            raise McpProtocolError(-32602, f"Unknown tool: {name}")
        self._tool_calls += 1
        if self._tool_calls > self._max_tool_calls:
            return _success(
                request_id,
                _tool_result(
                    {"error": "MCP session tool-call limit exceeded"},
                    is_error=True,
                ),
            )
        try:
            result = self._dispatcher.call(name, params.get("arguments", {}))
        except psycopg.Error:
            result_payload = _tool_result(
                {"error": "Database operation failed"},
                is_error=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            result_payload = _tool_result({"error": str(error)}, is_error=True)
        else:
            result_payload = _tool_result(result, is_error=False)
        return _success(request_id, result_payload)


def _changed_line_coverage(
    args: dict[str, object],
) -> ChangedLineCoverageEvidence | None:
    value = args.get("changed_line_coverage")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("changed_line_coverage must be an object")
    return ChangedLineCoverageEvidence.from_dict(cast(dict[str, Any], value))


def _text(args: dict[str, object], key: str) -> str:
    return str(args[key])


def _tool_result(payload: dict[str, object], *, is_error: bool) -> dict[str, object]:
    text = _json(payload)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _success(request_id: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(
    request_id: object,
    code: int,
    message: str,
    data: object | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _valid_request_id(value: object) -> bool:
    return value is None or (isinstance(value, (int, str)) and not isinstance(value, bool))


def _public_flow_status(flow: dict[str, object]) -> dict[str, object]:
    """Return only the six-stage business projection to the Copilot client."""

    return {
        key: flow.get(key)
        for key in (
            "status",
            "current_stage",
            "progress_percent",
            "blocking_reasons",
        )
    }


def _public_change_output(
    result: dict[str, object],
    *,
    output_stage: str,
) -> dict[str, object]:
    """Return the accepted business output without Canonical implementation IDs."""

    public = {
        key: result.get(key)
        for key in ("recorded_stage", "next_stage", "coding_task_state")
    }
    if output_stage == "document_change":
        public["document_count"] = len(cast(list[object], result.get("document_ids", [])))
        public["document_change_count"] = len(
            cast(list[object], result.get("document_change_refs", []))
        )
    elif output_stage == "code_scope":
        public["code_scope"] = result.get("code_scope", [])
    elif output_stage == "test_planning":
        public["test_plan_id"] = result.get("test_plan_id")
        public["test_data_plan_id"] = result.get("test_data_plan_id")
    else:
        raise ValueError(f"Unsupported public Change Task output stage: {output_stage}")
    return public


def _public_command_result(result: dict[str, object]) -> dict[str, object]:
    """Return command outcome and digest Evidence, never Profile or path internals."""

    return {
        key: result.get(key)
        for key in (
            "command_execution_id",
            "created",
            "command_ref",
            "status",
            "exit_code",
            "stdout_digest",
            "stderr_digest",
            "stdout_bytes",
            "stderr_bytes",
            "output_truncated",
            "started_at",
            "completed_at",
            "coding_task_state",
        )
    }


def _public_edit_result(result: dict[str, object]) -> dict[str, object]:
    """Return path, test, revision, and coverage outcomes without control-plane state."""

    public = {
        key: result.get(key)
        for key in (
            "edit_result_id",
            "created",
            "status",
            "command_evidence_status",
            "changed_paths",
            "out_of_scope_files",
            "result_repository_revision",
            "coding_task_state",
        )
    }
    coverage = result.get("changed_line_coverage")
    if isinstance(coverage, dict):
        public["changed_line_coverage"] = {
            key: coverage.get(key)
            for key in (
                "minimum_coverage_percent",
                "changed_line_count",
                "covered_changed_line_count",
                "coverage_percent",
                "files",
                "evidence_refs",
                "status",
                "blocking_reasons",
            )
        }
    return public


def _require_request_id(message: dict[str, object]) -> None:
    if "id" not in message or message["id"] is None:
        raise McpProtocolError(-32600, "Request ID is required")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
