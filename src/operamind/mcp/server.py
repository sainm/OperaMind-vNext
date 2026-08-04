"""SDK-free MCP 2025-11-25 stdio server for bounded Copilot tools."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO, cast

import psycopg
from jsonschema import Draft202012Validator
from psycopg import Connection

from operamind.application import CopilotCodingTaskService
from operamind.application.change_automation import CHANGE_FLOW_STATE_MACHINE
from operamind.application.copilot_document_change import DocumentFieldEdit
from operamind.application.document_profile_learning import DocumentProfileLearningService
from operamind.contracts import ContractCatalog
from operamind.contracts.catalog import ArtifactValidationError

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


def _artifact_input_schema(artifact_type: str, property_name: str) -> dict[str, object]:
    """Expose one canonical Artifact contract as an MCP tool input subschema."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    resource_root = (
        Path(frozen_root).resolve()
        if isinstance(frozen_root, str) and frozen_root
        else Path(__file__).resolve().parents[3]
    )
    schema_path = resource_root / "contracts" / "schemas" / f"{artifact_type}.schema.json"
    schema = cast(dict[str, object], json.loads(schema_path.read_text(encoding="utf-8")))
    schema.pop("$schema", None)
    schema.pop("$id", None)
    schema.pop("title", None)
    return cast(
        dict[str, object],
        _rewrite_local_schema_refs(schema, f"#/properties/{property_name}"),
    )


def _rewrite_local_schema_refs(value: object, root_ref: str) -> object:
    if isinstance(value, dict):
        return {
            key: (
                f"{root_ref}{item[1:]}"
                if key == "$ref" and isinstance(item, str) and item.startswith("#/")
                else _rewrite_local_schema_refs(item, root_ref)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_local_schema_refs(item, root_ref) for item in value]
    return value


def _change_outputs_schema() -> dict[str, object]:
    schema = _schema(
        {
            "coding_task_id": _string(),
            "workspace_root": _string(),
            "consumer_id": _string(),
            "claim_token": _string(),
            "output_stage": {
                "enum": [
                    "document_change",
                    "code_scope",
                    "test_planning",
                    "ui_test_revision",
                    "document_profile_learning",
                ]
            },
            "document_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": _string(),
            },
            "document_edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "document_id",
                        "stable_key",
                        "field",
                        "new_value",
                    ],
                    "properties": {
                        "document_id": _string(),
                        "stable_key": _string(),
                        "field": _string(),
                        "new_value": _string(),
                    },
                },
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
                        "recommended_action": {"enum": ["modify", "add", "delete", "review_only"]},
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
            "test_plan": _artifact_input_schema("test-plan", "test_plan"),
            "test_data_plan": _artifact_input_schema("test-data-plan", "test_data_plan"),
            "document_profile_draft": _artifact_input_schema(
                "document-profile-learning-draft", "document_profile_draft"
            ),
        },
        ("coding_task_id", "workspace_root", "output_stage"),
    )
    schema["oneOf"] = [
        {
            "properties": {"output_stage": {"const": "document_change"}},
            "required": ["document_ids", "document_edits"],
        },
        {
            "properties": {"output_stage": {"const": "code_scope"}},
            "required": ["code_scope"],
        },
        {
            "properties": {"output_stage": {"const": "test_planning"}},
            "required": ["test_plan", "test_data_plan"],
        },
        {
            "properties": {"output_stage": {"const": "ui_test_revision"}},
            "required": ["test_plan", "test_data_plan"],
        },
        {
            "properties": {"output_stage": {"const": "document_profile_learning"}},
            "required": ["document_profile_draft", "consumer_id", "claim_token"],
        },
    ]
    return schema


TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "copilot_get_coding_task",
        "title": "Load one unified Copilot Change Task",
        "description": (
            "Load only the current stage of one confirmed Change Task. The response separates "
            "business inputs, machine constraints, the expected output, and the stop condition."
        ),
        "inputSchema": _schema(
            {
                "coding_task_id": _string(),
                "workspace_root": _string(),
                "consumer_id": _string(),
                "claim_token": _string(),
            },
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
            "result. Returns result plus the common stage_status envelope."
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
            "a Code Graph scope, or validate TestPlan/TestDataPlan after the code diff. Returns "
            "result plus stage_status; follow only stage_status.next_action."
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
            "automatically publish the path-only Diff result to OperaMind Web. Preserve the "
            "returned committed_edit_result_id for copilot_record_task_result; a working and "
            "committed validation are distinct immutable evidence records. Returns result plus "
            "the common stage_status envelope."
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
            "code result to OperaMind Web without a response file. For source changes, pass "
            "the approved coverage command ID; OperaMind reads and verifies its bound report "
            "before the UI TestPlan stage can start. edit_result_id must be the distinct "
            "committed_edit_result_id returned by copilot_validate_task_diff. Returns result "
            "plus stage_status; reload only when next_action is reload_current_task."
        ),
        "inputSchema": _schema(
            {
                "coding_task_id": _string(),
                "workspace_root": _string(),
                "edit_result_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The distinct committed_edit_result_id returned by "
                        "copilot_validate_task_diff."
                    ),
                },
                "test_result_refs": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": _string(),
                },
                "tests_passed": {"type": "boolean"},
                "coverage_report_command_execution_id": _string(),
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
            key=lambda error: (list(error.absolute_path), error.message),
        )
        if errors:
            raise ValueError(_tool_validation_error_message(errors))
        args = cast(dict[str, object], arguments)
        if name == "copilot_run_task_command":
            # ApprovedCommandService persists the reservation before launching the
            # child process and records its result in a separate transaction.  An
            # outer transaction would retain Task/Grant locks for the full Gradle
            # runtime and can deadlock with the Web task-resume poller.
            return self._dispatch(name, args)
        with self._connection.transaction():
            return self._dispatch(name, args)

    def _dispatch(self, name: str, args: dict[str, object]) -> dict[str, object]:
        coding_tasks = CopilotCodingTaskService(
            connection=self._connection,
            repository_root=self._contracts.root.parent,
        )
        if name == "copilot_get_coding_task":
            coding_task_id = _text(args, "coding_task_id")
            if coding_task_id.startswith("document-learning-"):
                return _stage_context_envelope(
                    DocumentProfileLearningService(
                        connection=self._connection,
                        repository_root=self._contracts.root.parent,
                    ).mcp_context(
                        learning_run_id=coding_task_id,
                        workspace_root=Path(_text(args, "workspace_root")),
                        consumer_id=_text(args, "consumer_id"),
                        claim_token=_text(args, "claim_token"),
                    )
                )
            return _stage_context_envelope(
                coding_tasks.get_mcp_context(
                    coding_task_id=coding_task_id,
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            )
        if name == "copilot_run_task_command":
            command = _public_command_result(
                coding_tasks.run_command(
                    coding_task_id=_text(args, "coding_task_id"),
                    command_execution_id=_text(args, "command_execution_id"),
                    command_ref=_text(args, "command_ref"),
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            )
            task_view = coding_tasks.view(_text(args, "coding_task_id"))
            passed = command.get("status") == "passed" and command.get("exit_code") == 0
            return {
                "result": command,
                "stage_status": _public_stage_status(
                    task_view,
                    outcome="passed" if passed else "failed",
                    next_action="continue_current_stage" if passed else "resolve_blocker",
                    message=(
                        "必須 Command が成功しました。"
                        if passed
                        else "必須 Command が成功していません。"
                    ),
                ),
            }
        if name == "copilot_record_change_outputs":
            output_stage = _text(args, "output_stage")
            if output_stage == "document_profile_learning":
                result = DocumentProfileLearningService(
                    connection=self._connection,
                    repository_root=self._contracts.root.parent,
                ).record_draft(
                    learning_run_id=_text(args, "coding_task_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                    consumer_id=_text(args, "consumer_id"),
                    claim_token=_text(args, "claim_token"),
                    draft=cast(dict[str, Any], args["document_profile_draft"]),
                )
                return {
                    "result": {"learning": result["learning"]},
                    "stage_status": result["stage_status"],
                }
            internal_result = coding_tasks.record_change_outputs(
                coding_task_id=_text(args, "coding_task_id"),
                workspace_root=Path(_text(args, "workspace_root")),
                output_stage=output_stage,
                document_ids=tuple(
                    str(value) for value in cast(list[object], args.get("document_ids", []))
                ),
                document_edits=tuple(
                    DocumentFieldEdit(
                        document_id=_text(cast(dict[str, object], value), "document_id"),
                        stable_key=_text(cast(dict[str, object], value), "stable_key"),
                        field=_text(cast(dict[str, object], value), "field"),
                        new_value=_text(cast(dict[str, object], value), "new_value"),
                    )
                    for value in cast(list[object], args.get("document_edits", []))
                ),
                code_scope=tuple(
                    cast(dict[str, Any], value)
                    for value in cast(list[object], args.get("code_scope", []))
                ),
                test_plan=cast(dict[str, Any], args["test_plan"]) if "test_plan" in args else None,
                test_data_plan=cast(dict[str, Any], args["test_data_plan"])
                if "test_data_plan" in args
                else None,
            )
            output = _public_change_output(internal_result, output_stage=output_stage)
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
            flow = service.main_change_flow(str(task_artifact["change_request_id"]))
            refreshed_task = coding_tasks.view(_text(args, "coding_task_id"))
            return {
                "result": output,
                "stage_status": _accepted_stage_status(
                    refreshed_task,
                    flow=flow,
                    message="現在工程の成果物を受け付けました。",
                ),
            }
        if name == "copilot_validate_task_diff":
            edit = _public_edit_result(
                coding_tasks.validate_diff(
                    coding_task_id=_text(args, "coding_task_id"),
                    edit_result_id=_text(args, "edit_result_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                )
            )
            task_view = coding_tasks.view(_text(args, "coding_task_id"))
            accepted = _edit_diff_accepted(
                edit.get("status"),
                verification_only=coding_tasks.is_verification_only(
                    _text(args, "coding_task_id")
                ),
            )
            return {
                "result": edit,
                "stage_status": _public_stage_status(
                    task_view,
                    outcome="passed" if accepted else "blocked",
                    next_action="continue_current_stage" if accepted else "resolve_blocker",
                    message=(
                        "コード差分は許可範囲内です。"
                        if accepted
                        else "コード差分に許可範囲外の変更があります。"
                    ),
                ),
            }
        if name == "copilot_record_task_result":
            edit = _public_edit_result(
                coding_tasks.record_result(
                    coding_task_id=_text(args, "coding_task_id"),
                    edit_result_id=_text(args, "edit_result_id"),
                    workspace_root=Path(_text(args, "workspace_root")),
                    test_result_refs=tuple(
                        str(value) for value in cast(list[object], args["test_result_refs"])
                    ),
                    tests_passed=cast(bool, args["tests_passed"]),
                    coverage_report_command_execution_id=(
                        _text(args, "coverage_report_command_execution_id")
                        if "coverage_report_command_execution_id" in args
                        else None
                    ),
                )
            )
            from operamind.application.web_control_plane import WebControlPlaneService

            task_view = coding_tasks.view(_text(args, "coding_task_id"))
            task_artifact = cast(dict[str, object], task_view["task"])
            service = WebControlPlaneService(
                connection=self._connection,
                repository_root=self._contracts.root.parent,
            )
            flow = service.main_change_flow(str(task_artifact["change_request_id"]))
            refreshed_task = coding_tasks.view(_text(args, "coding_task_id"))
            return {
                "result": edit,
                "stage_status": _accepted_stage_status(
                    refreshed_task,
                    flow=flow,
                    message="コード変更、Command Evidence、Coverage を受け付けました。",
                ),
            }
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
        except ArtifactValidationError as error:
            details = [
                {
                    "location": issue.location,
                    "message": issue.message,
                }
                for issue in error.report.issues[:20]
            ]
            result_payload = _tool_result(
                {
                    "error": "Artifact validation failed",
                    "validation_issues": details,
                },
                is_error=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            result_payload = _tool_result({"error": str(error)}, is_error=True)
        else:
            result_payload = _tool_result(result, is_error=False)
        return _success(request_id, result_payload)


def _tool_validation_error_message(errors: list[Any], *, limit: int = 20) -> str:
    details = []
    for error in errors[:limit]:
        location = "/".join(str(part) for part in error.absolute_path) or "$"
        message = error.message
        if (
            error.validator == "additionalProperties"
            and isinstance(error.instance, dict)
            and isinstance(error.schema, dict)
        ):
            allowed = set(error.schema.get("properties", {}))
            unexpected = sorted(set(error.instance) - allowed)
            if unexpected:
                message = f"unexpected properties {unexpected}; allowed={sorted(allowed)}"
        details.append(f"{location}: {message}")
    remaining = len(errors) - len(details)
    suffix = f"; ... and {remaining} more" if remaining else ""
    return "Invalid tool arguments: " + "; ".join(details) + suffix


def _text(args: dict[str, object], key: str) -> str:
    return str(args[key])


def _tool_result(payload: dict[str, object], *, is_error: bool) -> dict[str, object]:
    return {
        "content": [{"type": "text", "text": _tool_result_summary(payload, is_error=is_error)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _tool_result_summary(payload: dict[str, object], *, is_error: bool) -> str:
    """Keep Copilot's visible tool transcript short; structuredContent remains authoritative."""

    if is_error:
        reason = str(payload.get("error") or "処理を続行できません。")
        return f"OperaMind: {reason}"
    stage_status = payload.get("stage_status")
    if isinstance(stage_status, dict):
        message = stage_status.get("message")
        if isinstance(message, str) and message.strip():
            return f"OperaMind: {message}"
    stage_contract = payload.get("stage_contract")
    if isinstance(stage_contract, dict):
        label = stage_contract.get("label")
        if isinstance(label, str) and label.strip():
            return f"OperaMind: {label} の入力と制約を取得しました。"
    return "OperaMind: 処理が完了しました。"


def _edit_diff_accepted(status: object, *, verification_only: bool) -> bool:
    """Accept an empty Diff only when the confirmed scope is intentionally read-only."""

    return status == "in_scope" or (status == "no_changes" and verification_only)


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


def _public_stage_status(
    task_view: dict[str, object],
    *,
    outcome: str,
    next_action: str,
    message: str,
    flow: dict[str, object] | None = None,
) -> dict[str, object]:
    """Use one compact stage envelope for every state-changing MCP result."""

    blocking_reasons = [
        str(value) for value in cast(list[object], (flow or {}).get("blocking_reasons", []))
    ]
    return {
        "task_stage": task_view.get("current_stage"),
        "flow_stage": (flow or {}).get("current_stage"),
        "task_state": task_view.get("state"),
        "outcome": outcome,
        "requires_confirmation": _flow_requires_confirmation(flow),
        "next_action": next_action,
        "message": message,
        "blocking_reasons": blocking_reasons,
    }


def _stage_context_envelope(context: dict[str, object]) -> dict[str, object]:
    """Apply the same result/status envelope to the read-only task load tool."""

    stage_status = context.get("stage_status")
    if not isinstance(stage_status, dict):
        raise RuntimeError("Copilot Change Task stage_status is missing")
    return {
        "result": {key: value for key, value in context.items() if key != "stage_status"},
        "stage_status": stage_status,
    }


def _accepted_stage_status(
    task_view: dict[str, object],
    *,
    flow: dict[str, object],
    message: str,
) -> dict[str, object]:
    blockers = cast(list[object], flow.get("blocking_reasons", []))
    confirmation = _flow_requires_confirmation(flow)
    task_state = task_view.get("state")
    if blockers or flow.get("status") == "blocked":
        outcome = "blocked"
        next_action = "resolve_blocker"
    elif confirmation:
        outcome = "accepted"
        next_action = "wait_for_confirmation"
        message += " OperaMind Web の確認を待ってください。"
    elif task_state in {"completed", "cancelled", "failed"}:
        outcome = "completed" if task_state == "completed" else "failed"
        next_action = "stop"
    else:
        outcome = "accepted"
        next_action = "reload_current_task"
        message += " 同じ Task ID で現在工程を再取得してください。"
    return _public_stage_status(
        task_view,
        outcome=outcome,
        next_action=next_action,
        message=message,
        flow=flow,
    )


def _flow_requires_confirmation(flow: dict[str, object] | None) -> bool:
    return CHANGE_FLOW_STATE_MACHINE.flow_requires_confirmation(flow)


def _public_change_output(
    result: dict[str, object],
    *,
    output_stage: str,
) -> dict[str, object]:
    """Return the accepted business output without Canonical implementation IDs."""

    public: dict[str, object] = {"output_stage": output_stage}
    if output_stage == "document_change":
        public["document_count"] = len(cast(list[object], result.get("document_ids", [])))
        public["document_change_count"] = len(
            cast(list[object], result.get("document_change_refs", []))
        )
    elif output_stage == "code_scope":
        public["code_scope"] = result.get("code_scope", [])
    elif output_stage in {"test_planning", "ui_test_revision"}:
        public["test_plan_id"] = result.get("test_plan_id")
        public["test_data_plan_id"] = result.get("test_data_plan_id")
        if output_stage == "ui_test_revision":
            public["revision_id"] = result.get("revision_id")
    else:
        raise ValueError(f"Unsupported public Change Task output stage: {output_stage}")
    return public


def _public_command_result(result: dict[str, object]) -> dict[str, object]:
    """Return command outcome and digest Evidence, never Profile or path internals."""

    public = {
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
        )
    }
    return public


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
            "committed_edit_result_id",
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
