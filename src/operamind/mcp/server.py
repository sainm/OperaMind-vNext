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
    ApprovedCommandRequest,
    ApprovedCommandService,
    ControlPlaneQueryService,
    CopilotCodingTaskService,
    CopilotHandoffRequest,
    CopilotHandoffService,
    EditResultRequest,
    EditResultService,
    EditValidationMode,
)
from operamind.contracts import ContractCatalog
from operamind.infrastructure.postgres import ApprovalGrantRepository
from operamind.profiles import ProfileCatalog

MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "operamind-vnext"
SERVER_VERSION = "0.1.0.dev0"
MCP_TOOL_NAME_PATTERN = re.compile(r"^[a-z0-9_-]+$")


def _string() -> dict[str, object]:
    return {"type": "string", "minLength": 1}


COMMON_SCOPE_PROPERTIES: dict[str, object] = {
    "project_id": _string(),
    "analysis_case_id": _string(),
    "edit_packet_id": _string(),
    "approval_grant_id": _string(),
    "workspace_root": _string(),
}


def _schema(properties: Mapping[str, object], required: tuple[str, ...]) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "analysis_list_ready_cases",
        "title": "List cases for this exact Workspace revision",
        "description": (
            "List at most 50 non-terminal analysis cases whose registered Workspace root, "
            "origin, and repository revision match the current clean Git checkout."
        ),
        "inputSchema": _schema(
            {
                "workspace_root": _string(),
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ("workspace_root",),
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "impact_get_report",
        "title": "Get one immutable Impact Report",
        "description": (
            "Return one Project/Case-scoped immutable ImpactReport plus its current "
            "normalized lifecycle status."
        ),
        "inputSchema": _schema(
            {
                "project_id": _string(),
                "analysis_case_id": _string(),
                "impact_report_id": _string(),
            },
            ("project_id", "analysis_case_id", "impact_report_id"),
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_get_edit_packet",
        "title": "Get approved Copilot edit handoff",
        "description": (
            "Return one active Edit Packet and Approval Grant after validating the local "
            "Workspace root, origin, and Git HEAD. Never returns the Context Package."
        ),
        "inputSchema": _schema(
            COMMON_SCOPE_PROPERTIES,
            tuple(COMMON_SCOPE_PROPERTIES),
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_get_approval_grant",
        "title": "Inspect one Approval Grant",
        "description": "Return the bounded actions and current lifecycle state of one Grant.",
        "inputSchema": _schema(
            {"project_id": _string(), "approval_grant_id": _string()},
            ("project_id", "approval_grant_id"),
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_run_approved_command",
        "title": "Run one approved command template",
        "description": (
            "Run a Grant-whitelisted command ref using its fixed Profile version, no shell, "
            "and return only digest-based execution evidence."
        ),
        "inputSchema": _schema(
            {
                **COMMON_SCOPE_PROPERTIES,
                "command_execution_id": _string(),
                "command_ref": _string(),
            },
            (*COMMON_SCOPE_PROPERTIES, "command_execution_id", "command_ref"),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_validate_worktree",
        "title": "Validate current worktree paths",
        "description": (
            "Record a working-tree Edit Result after comparing all changed paths to the "
            "active Packet allowlist."
        ),
        "inputSchema": _schema(
            {**COMMON_SCOPE_PROPERTIES, "edit_result_id": _string()},
            (*COMMON_SCOPE_PROPERTIES, "edit_result_id"),
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "copilot_record_edit_result",
        "title": "Record committed edit result",
        "description": (
            "Validate a clean committed worktree and bind the result to audited command "
            "execution IDs from the same Grant and Packet."
        ),
        "inputSchema": _schema(
            {
                **COMMON_SCOPE_PROPERTIES,
                "edit_result_id": _string(),
                "test_result_refs": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": _string(),
                },
                "tests_passed": {"type": "boolean"},
            },
            (
                *COMMON_SCOPE_PROPERTIES,
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
    {
        "name": "copilot_get_coding_task",
        "title": "Load one user-confirmed Coding Plan task",
        "description": (
            "Load the transport-neutral task, active Edit Packet, Grant, and bounded "
            "Coding Plan after the VS Code user has confirmed the local Bridge notification."
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
            "Run one Grant-whitelisted command and automatically publish its digest-only "
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
        "name": "copilot_validate_task_diff",
        "title": "Validate and publish the current Coding Task diff",
        "description": (
            "Compare all current Git path changes with the Task Packet allowlist and "
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
            "final result to OperaMind Web without a response file."
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
    {
        "name": "verification_get_ui_plan",
        "title": "Get one UI execution plan",
        "description": (
            "Return one Project-scoped UI Plan with fixed Deployment revision and approved "
            "Scenario version IDs, without raw evidence content."
        ),
        "inputSchema": _schema(
            {"project_id": _string(), "plan_id": _string()},
            ("project_id", "plan_id"),
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "validation_get_result",
        "title": "Get one final change validation",
        "description": (
            "Return one Project-scoped UiVerificationResult and normalized closure state, "
            "without screenshot or log bytes."
        ),
        "inputSchema": _schema(
            {"project_id": _string(), "verification_result_id": _string()},
            ("project_id", "verification_result_id"),
        ),
        "annotations": {
            "readOnlyHint": True,
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
        self._profiles = ProfileCatalog.load(root.resolve() / "profiles")

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
        queries = ControlPlaneQueryService(
            connection=self._connection,
            contracts=self._contracts,
        )
        if name == "analysis_list_ready_cases":
            return queries.list_ready_cases(
                workspace_root=Path(_text(args, "workspace_root")),
                limit=cast(int, args.get("limit", 20)),
            )
        if name == "impact_get_report":
            return queries.get_impact_report(
                project_id=_text(args, "project_id"),
                analysis_case_id=_text(args, "analysis_case_id"),
                impact_report_id=_text(args, "impact_report_id"),
            )
        if name == "copilot_get_edit_packet":
            return CopilotHandoffService(
                connection=self._connection,
                contracts=self._contracts,
            ).get(_handoff_request(args))
        if name == "copilot_get_approval_grant":
            return self._get_grant(args)
        if name == "copilot_run_approved_command":
            return (
                ApprovedCommandService(
                    connection=self._connection,
                    contracts=self._contracts,
                    profiles=self._profiles,
                )
                .run(
                    ApprovedCommandRequest(
                        command_execution_id=_text(args, "command_execution_id"),
                        approval_grant_id=_text(args, "approval_grant_id"),
                        project_id=_text(args, "project_id"),
                        analysis_case_id=_text(args, "analysis_case_id"),
                        edit_packet_id=_text(args, "edit_packet_id"),
                        workspace_root=Path(_text(args, "workspace_root")),
                        command_ref=_text(args, "command_ref"),
                    )
                )
                .to_dict()
            )
        if name == "copilot_validate_worktree":
            return self._edit_result(args, mode=EditValidationMode.WORKING)
        if name == "copilot_record_edit_result":
            return self._edit_result(args, mode=EditValidationMode.COMMITTED)
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
            return coding_tasks.run_command(
                coding_task_id=_text(args, "coding_task_id"),
                command_execution_id=_text(args, "command_execution_id"),
                command_ref=_text(args, "command_ref"),
                workspace_root=Path(_text(args, "workspace_root")),
            )
        if name == "copilot_validate_task_diff":
            return coding_tasks.validate_diff(
                coding_task_id=_text(args, "coding_task_id"),
                edit_result_id=_text(args, "edit_result_id"),
                workspace_root=Path(_text(args, "workspace_root")),
            )
        if name == "copilot_record_task_result":
            return coding_tasks.record_result(
                coding_task_id=_text(args, "coding_task_id"),
                edit_result_id=_text(args, "edit_result_id"),
                workspace_root=Path(_text(args, "workspace_root")),
                test_result_refs=tuple(
                    str(value) for value in cast(list[object], args["test_result_refs"])
                ),
                tests_passed=cast(bool, args["tests_passed"]),
            )
        if name == "verification_get_ui_plan":
            return queries.get_ui_plan(
                project_id=_text(args, "project_id"),
                plan_id=_text(args, "plan_id"),
            )
        if name == "validation_get_result":
            return queries.get_validation_result(
                project_id=_text(args, "project_id"),
                verification_result_id=_text(args, "verification_result_id"),
            )
        raise AssertionError(f"Tool dispatch is incomplete: {name}")

    def _get_grant(self, args: dict[str, object]) -> dict[str, object]:
        grant = ApprovalGrantRepository(self._connection, self._contracts).inspect(
            _text(args, "approval_grant_id")
        )
        if grant.project_id != _text(args, "project_id"):
            raise ValueError("Approval Grant is outside requested Project scope")
        return {
            "approval_grant_id": grant.grant_id,
            "project_id": grant.project_id,
            "analysis_case_id": grant.analysis_case_id,
            "edit_packet_id": grant.edit_packet_id,
            "base_repository_revision": grant.base_repository_revision,
            "allowed_actions": list(grant.allowed_actions),
            "command_profile_version_id": grant.command_profile_version_id,
            "allowed_test_command_refs": list(grant.allowed_test_command_refs),
            "allowed_ui_scenarios": list(grant.allowed_ui_scenarios),
            "expires_at": grant.expires_at.isoformat(),
            "state": grant.state,
        }

    def _edit_result(
        self, args: dict[str, object], *, mode: EditValidationMode
    ) -> dict[str, object]:
        refs = (
            tuple(str(value) for value in cast(list[object], args["test_result_refs"]))
            if mode is EditValidationMode.COMMITTED
            else ()
        )
        tests_passed = cast(bool, args["tests_passed"]) if refs else None
        return (
            EditResultService(
                connection=self._connection,
                contracts=self._contracts,
            )
            .run(
                EditResultRequest(
                    edit_result_id=_text(args, "edit_result_id"),
                    edit_packet_id=_text(args, "edit_packet_id"),
                    approval_grant_id=_text(args, "approval_grant_id"),
                    project_id=_text(args, "project_id"),
                    analysis_case_id=_text(args, "analysis_case_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                    mode=mode,
                    test_result_refs=refs,
                    tests_passed=tests_passed,
                )
            )
            .to_dict()
        )


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
                    "description": "Bounded local Copilot editing and test handoff",
                },
                "instructions": (
                    "Use only the returned Edit Packet and Approval Grant. Never request or "
                    "submit arbitrary shell commands or files outside the Packet."
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


def _handoff_request(args: dict[str, object]) -> CopilotHandoffRequest:
    return CopilotHandoffRequest(
        project_id=_text(args, "project_id"),
        analysis_case_id=_text(args, "analysis_case_id"),
        edit_packet_id=_text(args, "edit_packet_id"),
        approval_grant_id=_text(args, "approval_grant_id"),
        workspace_root=Path(_text(args, "workspace_root")),
    )


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


def _require_request_id(message: dict[str, object]) -> None:
    if "id" not in message or message["id"] is None:
        raise McpProtocolError(-32600, "Request ID is required")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
