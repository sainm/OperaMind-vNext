from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from operamind.application.visiondemo_target_e2e import (
    build_visiondemo_cross_screen_plan,
)
from operamind.web.app import create_app
from operamind.web.dependencies import get_service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPERAMIND_PLAYWRIGHT_LIVE") != "1",
        reason="OPERAMIND_PLAYWRIGHT_LIVE is not set",
    ),
]

ROOT = Path(__file__).parents[2]


class _ManagementUiService:
    def __init__(self, screenshot: Path) -> None:
        self._screenshot = screenshot
        self._plan = build_visiondemo_cross_screen_plan()
        self._test_plan = {
            "test_cases": [
                {
                    "test_case_id": "cross-screen-ui-case",
                    "title": "社員と経費の関連を画面横断で確認する",
                    "level": "ui",
                    "preconditions": ["社員と経費の関連データが生成済み"],
                    "steps": ["社員一覧を開く", "経費一覧を開く"],
                    "expected_results": ["同じ社員の経費が表示される"],
                    "test_data_refs": ["visiondemo-cross-screen-runtime-data"],
                },
                {
                    "test_case_id": "expense-summary-case",
                    "title": "経費集計を確認する",
                    "level": "ui",
                    "preconditions": ["経費データが生成済み"],
                    "steps": ["経費集計を開く"],
                    "expected_results": ["合計 4 件を表示する"],
                    "test_data_refs": ["visiondemo-cross-screen-runtime-data"],
                },
            ]
        }
        self._revision_applied = False
        self._revision_undone = False
        self._execution_authorized = False
        self._new_run_completed = False
        self._reviewed_knowledge: dict[str, dict[str, object]] = {}
        self._coverage = {
            "coverage_percent": 100,
            "covered_rule_count": 1,
            "business_rule_count": 1,
            "items": [
                {
                    "business_rule_id": "社員と経費の画面横断関連を確認する",
                    "test_case_refs": ["画面横断テスト"],
                    "criterion_refs": ["社員名と経費番号が一致する"],
                    "status": "covered",
                }
            ],
        }

    def readiness(self) -> dict[str, object]:
        return {
            "readiness_stage": "partial_ready",
            "manifest_status": "pending",
            "gates": [],
        }

    def list_projects(self) -> dict[str, object]:
        return {
            "projects": [
                {
                    "project_id": "visiondemo",
                    "name": "VisionDemo",
                    "change_request_count": 1,
                }
            ],
            "count": 1,
        }

    def orchestration_task_management(self, **values: object) -> dict[str, object]:
        assert values["project_id"] == "visiondemo"
        return {"tasks": [], "count": 0}

    def orchestration_task_dependency_graph(self, **values: object) -> dict[str, object]:
        assert values["project_id"] == "visiondemo"
        return {"tasks": [], "count": 0, "total_count": 0, "truncated": False}

    def orchestration_task_runtime_monitoring(self, **values: object) -> dict[str, object]:
        assert values == {"project_id": "visiondemo", "window_hours": 24}
        return {
            "window_hours": 24,
            "project_id": "visiondemo",
            "task_count": 8,
            "ready_count": 2,
            "active_task_count": 1,
            "claim_count": 7,
            "average_queue_wait_seconds": 12.4,
            "p95_queue_wait_seconds": 45.0,
            "average_execution_seconds": 83.0,
            "success_count": 5,
            "result_count": 6,
            "success_rate": 0.8333,
            "retry_count": 2,
            "retried_task_count": 1,
            "lease_expiry_count": 1,
            "blocker_reasons": [
                {
                    "reason": "業務担当者の確認待ち",
                    "occurrence_count": 2,
                    "latest_at": "2026-07-21T10:00:00+00:00",
                }
            ],
            "workers": [
                {
                    "executor_kind": "agent",
                    "executor_id": "worker-web-1",
                    "capabilities": ["requirement_review", "impact_review"],
                    "project_id": "visiondemo",
                    "max_concurrent_tasks": 1,
                    "active_task_count": 1,
                    "status": "online",
                    "present": True,
                    "live": True,
                    "registered_at": "2026-07-21T09:00:00+00:00",
                    "last_seen_at": "2026-07-21T10:00:00+00:00",
                    "lease_expires_at": "2026-07-21T10:01:00+00:00",
                    "events": [
                        {
                            "sequence": 1,
                            "event_type": "registered",
                            "actor": "worker-web-1",
                            "payload": {},
                            "created_at": "2026-07-21T09:00:00+00:00",
                        }
                    ],
                }
            ],
        }

    def unresolved_evidence_management(
        self, *, project_id: str, history_limit: int = 50
    ) -> dict[str, object]:
        assert project_id == "visiondemo"
        assert history_limit == 50
        report = {
            "artifact_type": "UnresolvedEvidenceReport",
            "schema_version": "v1",
            "unresolved_evidence_report_id": "unresolved-report-current",
            "project_id": project_id,
            "repository_id": "repository-web-demo",
            "repository_revision": "revision-web-demo",
            "code_graph_snapshot_id": "graph-web-demo",
            "report_status": "needs_evidence",
            "trigger": {
                "trigger_type": "static_graph",
                "evidence_refs": ["graph-web-demo"],
            },
            "open_count": 1,
            "closed_count": 0,
            "items": [
                {
                    "item_id": "unresolved-item-route",
                    "finding_key": "finding-route",
                    "edge_ref": "edge-route",
                    "status": "open",
                    "category": "endpoint_route",
                    "reason": "runtime_observation_missing",
                    "edge_type": "calls",
                    "source_ref": "route-customer",
                    "unresolved_target_ref": "unresolved:endpoint:GET:dynamic:customerUrl",
                    "source_location": {
                        "path": "src/web/customer.js",
                        "start_line": 18,
                        "end_line": 18,
                    },
                    "candidate_targets": [],
                    "missing_evidence": ["runtime_route_observation"],
                    "resolution_suggestions": ["collect_runtime_route"],
                    "provenance": "static",
                    "evidence_refs": [],
                }
            ],
        }
        return {
            "project_id": project_id,
            "current_reports": [report],
            "history": [
                {
                    "unresolved_evidence_report_id": "unresolved-report-current",
                    "repository_id": "repository-web-demo",
                    "code_graph_snapshot_id": "graph-web-demo",
                    "report_status": "needs_evidence",
                    "trigger_type": "static_graph",
                    "open_count": 1,
                    "closed_count": 0,
                    "created_at": "2026-07-20T09:00:00+00:00",
                    "is_current": True,
                }
            ],
            "current_report_count": 1,
            "history_count": 1,
            "open_count": 1,
            "closed_in_current_count": 0,
        }

    def list_change_requests(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        return {
            "project_id": project_id,
            "count": 1,
            "change_requests": [
                {
                    "change_request_id": "cross-screen-change",
                    "requirement_text": "社員画面と経費画面を関連データで検証する",
                    "document_review_status": "confirmed",
                    "submitted_by": "qa-user",
                }
            ],
        }

    def ui_knowledge_review_queue(self, *, project_id: str) -> dict[str, object]:
        assert project_id == "visiondemo"
        drafts = [
            _ui_knowledge_draft(
                snapshot_id="knowledge-draft-approved",
                version="2.0.0-draft",
                status="unique_visible",
                match_count=1,
                visible_count=1,
                reliability=0.98,
                evidence_url=(
                    "/api/v1/projects/visiondemo/ui-knowledge/reviews/"
                    "knowledge-draft-approved/screenshots/knowledge-screen"
                ),
            ),
            _ui_knowledge_draft(
                snapshot_id="knowledge-draft-rejected",
                version="2.1.0-draft",
                status="ambiguous",
                match_count=2,
                visible_count=2,
                reliability=0.72,
                evidence_url=(
                    "/api/v1/projects/visiondemo/ui-knowledge/reviews/"
                    "knowledge-draft-rejected/screenshots/knowledge-screen"
                ),
            ),
        ]
        pending = [
            draft for draft in drafts if draft["snapshot_id"] not in self._reviewed_knowledge
        ]
        versions: list[dict[str, object]] = [
            {
                "snapshot_id": "knowledge-source",
                "snapshot_version": "1.0.0",
                "review_status": "approved",
                "reviewed_by": "previous-qa",
                "environment_id": "visiondemo-local",
                "deployment_revision": "visiondemo-revision",
                "active": False,
                "reason": "初期 UI Knowledge",
            }
        ]
        versions.extend(self._reviewed_knowledge.values())
        return {
            "project_id": project_id,
            "draft_count": len(pending),
            "drafts": pending,
            "versions": versions,
        }

    def review_ui_knowledge(self, **values: object) -> dict[str, object]:
        assert values["project_id"] == "visiondemo"
        assert values["actor"] == "qa-user"
        source = str(values["source_snapshot_id"])
        decision = str(values["decision"])
        active = bool(values["activate"])
        assert not active or decision == "approved"
        result_id = f"{source}-{decision}"
        self._reviewed_knowledge[source] = {
            "snapshot_id": result_id,
            "snapshot_version": str(values["result_snapshot_version"]),
            "review_status": decision,
            "reviewed_by": str(values["actor"]),
            "environment_id": "visiondemo-local",
            "deployment_revision": "visiondemo-revision",
            "active": active,
            "reason": str(values["reason"]),
        }
        return {
            "created": True,
            "review_event_id": f"review-{source}",
            "source_snapshot_id": source,
            "result_snapshot_id": result_id,
            "result_snapshot_version": values["result_snapshot_version"],
            "decision": decision,
            "active": active,
            "reviewed_by": values["actor"],
            "reason": values["reason"],
        }

    def ui_knowledge_screenshot_path(self, **values: str) -> Path:
        assert values["project_id"] == "visiondemo"
        assert values["snapshot_id"] in {
            "knowledge-draft-approved",
            "knowledge-draft-rejected",
        }
        assert values["evidence_id"] == "knowledge-screen"
        return self._screenshot

    def get_change_request(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        return {
            "change_request_id": request_id,
            "project_id": "visiondemo",
            "analysis_case_id": "cross-screen-case",
            "document_review": {"status": "confirmed"},
        }

    def document_diff(self, request_id: str) -> dict[str, object]:
        return {
            "request_id": request_id,
            "change_request": {"document_review": {"status": "confirmed"}},
            "changes": [],
            "total": 0,
        }

    def case_detail(self, *, project_id: str, case_id: str) -> dict[str, object]:
        assert (project_id, case_id) == ("visiondemo", "cross-screen-case")
        return {
            "progress": {
                "confirmation": {"id": "confirmation-001"},
                "edit_packet": {"id": None},
                "approval_grant": {"id": "grant-001"},
                "steps": [
                    {"label": "テストデータ実行", "status": "completed"},
                    {"label": "変更クローズ", "status": "blocked"},
                ],
            },
            "impact_report": None,
            "evidence": {"command_results": [], "ui_evidence": []},
        }

    def change_orchestration(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        return {
            "bundle": {
                "orchestration": {
                    "status": "ready",
                    "code_scope": [],
                    "ui_scenarios": [],
                },
                "test_data_plan": self._plan,
                "test_plan": self._test_plan,
                "coverage_report": self._coverage,
            }
        }

    def change_automation(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        return {"run": self._automation_run()}

    def copilot_task(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        return {"change_request_id": request_id, "task": None}

    def resume_change_automation(self, **values: object) -> dict[str, object]:
        assert values["request_id"] == "cross-screen-change"
        assert values["run_id"] == "automation-cross-screen"
        assert values["actor"] == "qa-user"
        return {"created": False, "run": self._automation_run()}

    def _automation_run(self) -> dict[str, object]:
        return {
            "automation_run_id": "automation-cross-screen",
            "current_stage": "ui_verification",
            "current_stage_label": "UI テスト・結果検証",
            "status": "waiting",
            "next_action": "run_ui_verification",
            "blocking_reason": None,
            "steps": [
                {"stage": "planning", "label": "コード・テスト編成", "status": "completed"},
                {"stage": "ui_verification", "label": "UI テスト・結果検証", "status": "waiting"},
            ],
            "events": [
                {
                    "sequence": 1,
                    "stage": "planning",
                    "status": "completed",
                    "message": "Case、データ、カバレッジ、UI シナリオを生成しました。",
                }
            ],
        }

    def test_case_modification_state(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        if not self._revision_applied:
            return {"latest": None, "history": []}
        operation = {
            "operation_id": "operation-step",
            "test_case_id": "cross-screen-ui-case",
            "case_title": "社員と経費の関連を画面横断で確認する",
            "field": "steps",
            "action": "replace",
            "summary_before": "テスト手順: 社員一覧を開く",
            "summary_after": "テスト手順: 社員検索結果を開く",
        }
        modification_revision = {
            "revision_id": "revision-applied",
            "revision_kind": "modification",
            "applied_by": "qa-user",
            "applied_operations": [operation],
            "stale_run_ids": ["run-old"],
            "stale_evidence_refs": ["evidence-old"],
            "stale_closure_result_ids": ["closure-old"],
        }
        if self._revision_undone:
            undo_operation = {
                **operation,
                "operation_id": "operation-undo",
                "action": "restore",
                "summary_before": operation["summary_after"],
                "summary_after": operation["summary_before"],
            }
            undo_revision = {
                "revision_id": "revision-undo",
                "revision_kind": "undo",
                "undo_of_revision_id": "revision-applied",
                "applied_by": "qa-user",
                "applied_operations": [undo_operation],
                "stale_run_ids": ["run-revised"],
                "stale_evidence_refs": ["evidence-revised"],
                "stale_closure_result_ids": ["closure-revised"],
            }
            return {
                "latest": {
                    "state": "applied",
                    "proposal": {
                        "proposal_kind": "undo",
                        "instruction": "改訂 revision-applied を取り消す",
                        "operations": [undo_operation],
                        "ambiguities": [],
                        "blocking_reasons": [],
                    },
                    "revision": undo_revision,
                },
                "history": [
                    {
                        "status": "current",
                        "can_undo": False,
                        "revision_kind": "undo",
                        "revision": undo_revision,
                    },
                    {
                        "status": "undone",
                        "can_undo": False,
                        "revision_kind": "modification",
                        "revision": modification_revision,
                    },
                ],
            }
        return {
            "latest": {
                "state": "applied",
                "proposal": {
                    "proposal_kind": "modification",
                    "instruction": "複数 Case を一括変更",
                    "operations": [operation],
                    "ambiguities": [],
                    "blocking_reasons": [],
                },
                "revision": modification_revision,
            },
            "history": [
                {
                    "status": "current",
                    "can_undo": True,
                    "revision_kind": "modification",
                    "revision": modification_revision,
                }
            ],
        }

    def modify_test_case(self, **values: object) -> dict[str, object]:
        assert values["request_id"] == "cross-screen-change"
        assert values["actor"] == "qa-user"
        return {
            "created": True,
            "state": "needs_confirmation",
            "proposal": {
                "proposal_id": "proposal-ambiguous",
                "proposal_kind": "modification",
                "instruction": values["instruction"],
                "operations": [
                    {
                        "operation_id": "operation-step",
                        "test_case_id": "cross-screen-ui-case",
                        "case_title": "社員と経費の関連を画面横断で確認する",
                        "field": "steps",
                        "action": "replace",
                        "summary_before": "テスト手順: 社員一覧を開く",
                        "summary_after": "テスト手順: 社員検索結果を開く",
                    },
                    {
                        "operation_id": "operation-data",
                        "test_case_id": "cross-screen-ui-case",
                        "case_title": "社員と経費の関連を画面横断で確認する",
                        "field": "test_data_values",
                        "action": "replace",
                        "summary_before": "テストデータ項目: employee_count = 1",
                        "summary_after": "テストデータ項目: employee_count = 2",
                    },
                    {
                        "operation_id": "operation-assertion",
                        "test_case_id": "expense-summary-case",
                        "case_title": "経費集計を確認する",
                        "field": "business_assertions",
                        "action": "replace",
                        "summary_before": "業務アサーション: 合計 4 件",
                        "summary_after": "業務アサーション: 合計 5 件",
                    },
                ],
                "blocking_reasons": [],
                "ambiguities": [
                    {
                        "ambiguity_id": "ambiguity-case",
                        "question": "どの期待結果を変更しますか?",
                        "options": [
                            {
                                "option_id": "option-keep",
                                "label": "現在の期待結果を保持する",
                                "operations": [
                                    {
                                        "operation_id": "operation-expected-keep",
                                        "test_case_id": "expense-summary-case",
                                        "case_title": "社員と経費の関連を画面横断で確認する",
                                        "field": "expected_results",
                                        "summary_before": "期待結果: 同じ社員の経費が表示される",
                                        "summary_after": "期待結果: 同じ社員の経費が表示される",
                                    }
                                ],
                            },
                            {
                                "option_id": "option-change",
                                "label": "社員名と経費番号の一致を確認する",
                                "operations": [
                                    {
                                        "operation_id": "operation-expected-change",
                                        "test_case_id": "expense-summary-case",
                                        "case_title": "社員と経費の関連を画面横断で確認する",
                                        "field": "expected_results",
                                        "summary_before": "期待結果: 同じ社員の経費が表示される",
                                        "summary_after": "期待結果: 社員名と経費番号が一致する",
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            "revision": None,
        }

    def confirm_test_case_modification(self, **values: object) -> dict[str, object]:
        assert values["proposal_id"] == "proposal-ambiguous"
        assert values["selections"] == {"ambiguity-case": "option-change"}
        self._revision_applied = True
        self._revision_undone = False
        self._test_plan["test_cases"][0]["steps"][0] = "社員検索結果を開く"
        self._test_plan["test_cases"][1]["expected_results"] = ["合計 5 件を表示する"]
        return {
            "created": True,
            "state": "applied",
            "proposal": {"proposal_id": "proposal-ambiguous"},
            "revision": {"revision_id": "revision-applied"},
        }

    def undo_test_case_revision(self, **values: object) -> dict[str, object]:
        assert values["request_id"] == "cross-screen-change"
        assert values["revision_id"] == "revision-applied"
        assert values["actor"] == "qa-user"
        self._revision_undone = True
        self._execution_authorized = False
        self._new_run_completed = False
        self._test_plan["test_cases"][0]["steps"][0] = "社員一覧を開く"
        self._test_plan["test_cases"][1]["expected_results"] = ["合計 4 件を表示する"]
        return {
            "created": True,
            "state": "applied",
            "revision": {
                "revision_id": "revision-undo",
                "revision_kind": "undo",
                "undo_of_revision_id": "revision-applied",
            },
        }

    def execution_management(self, request_id: str) -> dict[str, object]:
        assert request_id == "cross-screen-change"
        revised = self._revision_applied
        current_execution = (
            {
                "run_id": "run-revised" if revised else "run-cross-screen",
                "status": "passed",
                "result": _execution_result(self._plan),
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "run_started",
                        "status": "running",
                    },
                    {
                        "sequence": 2,
                        "event_type": "run_completed",
                        "status": "completed",
                    },
                ],
            }
            if not revised or self._new_run_completed
            else None
        )
        current_closure = (
            {
                "status": "blocked",
                "ui_status": "blocked",
                "business_coverage_percent": 100,
                "modified_paths": ["VisionDemo/src/main/java/ExpenseService.java"],
                "test_results": [
                    {
                        "test_case_id": "画面横断 UI テスト",
                        "status": "blocked",
                        "evidence_refs": [],
                        "summary": "Required UI Scenario has no verification result.",
                    }
                ],
                "unresolved_items": ["UI verification is blocked or missing"],
            }
            if not revised or self._new_run_completed
            else None
        )
        authorization = None
        comparison = None
        if revised:
            authorization = {
                "authorized": self._execution_authorized,
                "status": (
                    "reconfirmed" if self._execution_authorized else "confirmation_required"
                ),
                "approval_grant_id": "grant-001",
                "confirmed_by": "qa-user" if self._execution_authorized else None,
                "blocking_reason": (
                    None
                    if self._execution_authorized
                    else "Test Case execution scope requires confirmation"
                ),
                "scope_comparison": {
                    "target_scope_digest": "a" * 64,
                    "changed_dimensions": ["ui_scenarios", "execution_scope"],
                    "dimensions": [
                        {"dimension": "test_data", "status": "unchanged"},
                        {"dimension": "ui_scenarios", "status": "changed"},
                        {"dimension": "execution_scope", "status": "changed"},
                    ],
                },
            }
            comparison = {
                "source": {
                    "run_status": "passed",
                    "evidence_count": 1,
                    "closure_status": "blocked",
                    "coverage_percent": 100,
                },
                "target": {
                    "run_status": "passed" if self._new_run_completed else None,
                    "evidence_count": 1 if self._new_run_completed else 0,
                    "closure_status": ("blocked" if self._new_run_completed else None),
                    "coverage_percent": 100,
                },
                "deltas": [
                    {
                        "field": "evidence_count",
                        "before": 1,
                        "after": 1 if self._new_run_completed else 0,
                    }
                ],
            }
        return {
            "change_request_id": request_id,
            "test_data_plan": self._plan,
            "test_data_execution": current_execution,
            "business_coverage": self._coverage,
            "change_closure": current_closure,
            "screenshots": [
                {
                    "origin": "test_data",
                    "evidence_id": "cross-screen-shot",
                    "step_id": "verify-expense-screen",
                    "sha256": "a" * 64,
                    "content_url": (
                        "/api/v1/change-requests/cross-screen-change/screenshots/"
                        "test_data/cross-screen-shot"
                    ),
                }
            ]
            if not revised or self._new_run_completed
            else [],
            "execution_authorization": authorization,
            "version_result_comparison": comparison,
            "failure_management": _failure_management(
                can_rerun=not revised or self._new_run_completed
            ),
            "controls": {
                "can_start": (
                    revised and self._execution_authorized and not self._new_run_completed
                ),
                "can_recover": False,
                "can_rerun": not revised or self._new_run_completed,
                "is_revised_version": revised,
                "requires_scope_confirmation": (revised and not self._execution_authorized),
                "approval_grant_id": "grant-001",
                "cleanup_mode": "automatic",
                "blocking_reason": (
                    "Test Case execution scope requires confirmation"
                    if revised and not self._execution_authorized
                    else None
                ),
            },
        }

    def confirm_test_case_execution_scope(self, **values: object) -> dict[str, object]:
        assert values["approval_grant_id"] == "grant-001"
        assert values["target_scope_digest"] == "a" * 64
        assert values["actor"] == "qa-user"
        self._execution_authorized = True
        return {
            "created": True,
            "authorization_id": "authorization-001",
            "approval_grant_id": "grant-001",
            "decision": "reconfirmed",
            "confirmed_by": "qa-user",
        }

    def start_test_data_run(self, **values: object) -> dict[str, object]:
        assert values["request_id"] == "cross-screen-change"
        assert values["actor"] == "qa-user"
        self._new_run_completed = True
        return {
            "created": True,
            "run_id": "run-revised",
            "execution_result_id": "result-revised",
            "status": "passed",
            "replay_of_run_id": None,
            "background_required": False,
        }

    def screenshot_path(self, **values: str) -> Path:
        assert values == {
            "request_id": "cross-screen-change",
            "origin": "test_data",
            "evidence_id": "cross-screen-shot",
        }
        return self._screenshot


def _execution_result(plan: dict[str, Any]) -> dict[str, object]:
    flow_results: list[dict[str, object]] = []
    for flow in plan["generation_flows"]:
        flow_results.append(
            {
                "flow_id": flow["flow_id"],
                "status": "passed",
                "step_results": [_step_result(step) for step in flow["steps"]],
                "cleanup_results": [_step_result(step) for step in flow["cleanup_steps"]],
                "deferred_assertion_ids": [],
            }
        )
    return {
        "status": "passed",
        "cleanup_status": "passed",
        "flow_results": flow_results,
    }


def _ui_knowledge_draft(
    *,
    snapshot_id: str,
    version: str,
    status: str,
    match_count: int,
    visible_count: int,
    reliability: float,
    evidence_url: str,
) -> dict[str, object]:
    issues = (
        []
        if status == "unique_visible"
        else [
            {
                "target_ref": "expense.status-filter",
                "code": "candidate_ambiguous",
                "message": "Locator candidate matched multiple elements.",
            }
        ]
    )
    return {
        "snapshot_id": snapshot_id,
        "snapshot_version": version,
        "environment_id": "visiondemo-local",
        "deployment_revision": "visiondemo-revision",
        "review_status": "draft",
        "observation": {
            "run_id": f"observation-{snapshot_id}",
            "status": "completed" if not issues else "partial",
            "issues": issues,
        },
        "targets": [
            {
                "target_ref": "expense.status-filter",
                "business_name": "ステータス絞り込み",
                "screen_name": "経費一覧",
                "trigger_path": "/expenses",
                "evidence": {
                    "evidence_id": "knowledge-screen",
                    "content_digest": "b" * 64,
                    "content_url": evidence_url,
                    "available": True,
                },
                "candidates": [
                    {
                        "candidate_id": f"candidate-{snapshot_id}",
                        "locator": {
                            "strategy": "test_id",
                            "value": "status-filter",
                        },
                        "priority": 1,
                        "reliability_score": reliability,
                        "source": "runtime_observation",
                        "observation": {
                            "status": status,
                            "match_count": match_count,
                            "visible_count": visible_count,
                            "discovered": True,
                        },
                    }
                ],
            }
        ],
    }


def _step_result(step: dict[str, Any]) -> dict[str, object]:
    return {
        "step_id": step["step_id"],
        "status": "passed",
        "output_variables": [binding["variable"] for binding in step.get("output_bindings", [])],
        "evidence_refs": [],
    }


def _failure_management(*, can_rerun: bool) -> dict[str, object]:
    values = [
        ("test_data", "テストデータ生成に失敗しました", "API が 500 を返しました"),
        ("ui", "UI シナリオの検証に失敗しました", "期待する経費行が表示されません"),
        ("cleanup", "テストデータのクリーンアップに失敗しました", "関連経費を削除できません"),
        ("coverage", "業務ルールがテストでカバーされていません", "経費検索ルールが未カバーです"),
        ("closure", "変更をクローズできません", "UI 検証が完了していません"),
    ]
    return {
        "status": "attention_required",
        "failure_count": len(values),
        "failures": [
            {
                "failure_id": f"failure-{category}",
                "category": category,
                "status": "failed" if category != "closure" else "blocked",
                "stage": f"{category}-stage",
                "summary_ja": summary,
                "reason": reason,
                "run_id": "run-cross-screen",
            }
            for category, summary, reason in values
        ],
        "actions": {
            "can_recover": True,
            "recover_run_id": "run-cross-screen",
            "recovery_requires_reason": True,
            "can_rerun": can_rerun,
            "rerun_run_id": "run-cross-screen" if can_rerun else None,
        },
    }


def test_japanese_management_ui_renders_cross_screen_closure_e2e() -> None:
    screenshot = (
        ROOT / "readiness" / "evidence" / "visiondemo-cross-screen-final-expense-list-20260718.png"
    )
    assert screenshot.is_file()
    service = _ManagementUiService(screenshot)
    app = create_app(repository_root=ROOT, database_url="postgresql:///unused")
    app.dependency_overrides[get_service] = lambda: service
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    errors: list[str] = []

    def capture_console(message: Any) -> None:
        if message.type == "error" and not message.text.startswith("Failed to load resource:"):
            errors.append(message.text)

    def capture_response(response: Any) -> None:
        if response.status >= 400 and not response.url.endswith("/favicon.ico"):
            errors.append(f"HTTP {response.status}: {response.url}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("console", capture_console)
            page.on("response", capture_response)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
            expect(page.locator("#uiKnowledgePanel")).to_be_visible()
            expect(page.locator("#unresolvedEvidencePanel")).to_be_visible()
            expect(page.locator("#taskMonitoringMetrics")).to_contain_text("成功率")
            expect(page.locator("#taskMonitoringMetrics")).to_contain_text("83%")
            expect(page.locator("#taskWorkerStatus")).to_contain_text("worker-web-1")
            expect(page.locator("#taskWorkerStatus")).to_contain_text("オンライン")
            expect(page.locator("#taskBlockerRanking")).to_contain_text(
                "業務担当者の確認待ち"
            )
            expect(page.locator("#unresolvedEvidenceSummary")).to_contain_text("未解決 1 件")
            expect(page.locator("#unresolvedEvidenceReports")).to_contain_text(
                "実行時 Route 観測がありません"
            )
            expect(page.locator("#unresolvedEvidenceReports")).to_contain_text(
                "src/web/customer.js:18-18"
            )
            expect(page.locator("#unresolvedEvidenceReports")).to_contain_text(
                "ブラウザの Route 観測"
            )
            expect(page.locator("#unresolvedEvidenceReports")).to_contain_text(
                "対象画面を実行して Route Evidence を採取する"
            )
            expect(page.locator("#unresolvedEvidenceHistory")).to_contain_text(
                "graph-web-demo"
            )
            expect(page.locator("#uiKnowledgeSummary")).to_contain_text("レビュー待ち 2 件")
            approved_draft = page.locator('[data-snapshot-id="knowledge-draft-approved"]')
            expect(approved_draft).to_contain_text("ステータス絞り込み")
            expect(approved_draft).to_contain_text("経費一覧")
            expect(approved_draft).to_contain_text("Test ID · status-filter")
            expect(approved_draft).to_contain_text("一致 1 · 表示 1")
            expect(approved_draft).to_contain_text("信頼度 98%")
            knowledge_image = approved_draft.locator("img")
            knowledge_image.scroll_into_view_if_needed()
            expect(knowledge_image).to_be_visible()
            expect(knowledge_image).to_have_js_property("naturalWidth", 1280)
            rejected_draft = page.locator('[data-snapshot-id="knowledge-draft-rejected"]')
            expect(rejected_draft).to_contain_text("複数要素に一致します")
            expect(rejected_draft).to_contain_text("一致 2 · 表示 2")
            page.locator("#actor").fill("qa-user")
            approved_draft.locator(".ui-knowledge-review-reason").fill(
                "一意性、表示状態、証跡を確認しました"
            )
            approved_draft.locator(".ui-knowledge-approve").click()
            expect(page.locator("#uiKnowledgeSummary")).to_contain_text("レビュー待ち 1 件")
            expect(page.locator("#uiKnowledgeVersions")).to_contain_text("2.0.0")
            expect(page.locator("#uiKnowledgeVersions")).to_contain_text(
                "一意性、表示状態、証跡を確認しました"
            )
            rejected_draft = page.locator('[data-snapshot-id="knowledge-draft-rejected"]')
            rejected_draft.locator(".ui-knowledge-review-reason").fill(
                "候補が複数要素に一致するため却下します"
            )
            rejected_draft.locator(".ui-knowledge-reject").click()
            expect(page.locator("#uiKnowledgeSummary")).to_contain_text("レビュー待ち 0 件")
            expect(page.locator("#uiKnowledgeVersions")).to_contain_text("却下")
            expect(page.locator("#uiKnowledgeVersions")).to_contain_text(
                "候補が複数要素に一致するため却下します"
            )
            page.locator(".request-item").click()

            expect(page.locator("html")).to_have_attribute("lang", "ja")
            expect(page.locator("#automationPanel")).to_be_visible()
            expect(page.locator("#automationSummary")).to_contain_text("UI テスト・結果検証")
            expect(page.locator("#automationTimeline")).to_contain_text("コード・テスト編成")
            expect(page.locator("#automationEvents")).to_contain_text("UI シナリオ")
            expect(page.locator("#resumeAutomation")).to_be_visible()
            page.locator("#actor").fill("qa-user")
            page.locator("#resumeAutomation").click()
            expect(page.locator("#notice")).to_contain_text("一括編成を再開しました")
            expect(page.locator("#testDataManagementPanel")).to_be_visible()
            expect(page.locator("#failureManagementPanel")).to_be_visible()
            expect(page.locator("#failureManagementSummary")).to_contain_text(
                "5 件の失敗またはブロック理由"
            )
            for category in ("TestData", "UI", "Cleanup", "Coverage", "Closure"):
                expect(page.locator("#failureManagementList")).to_contain_text(category)
            expect(page.locator("#failureManagementList")).to_contain_text(
                "期待する経費行が表示されません"
            )
            expect(page.locator("#failureRecover")).to_be_visible()
            expect(page.locator("#failureRecoveryReason")).to_be_visible()
            expect(page.locator("#failureRecoveryReason")).to_have_attribute(
                "placeholder",
                "例：Worker 停止後、30 秒以上進捗が更新されていない",  # noqa: RUF001
            )
            page.locator("#failureRecover").click()
            expect(page.locator("#notice")).to_contain_text("中断理由を入力してください")
            expect(page.locator("#failureRerun")).to_be_visible()
            expect(page.locator("#failureRerun")).to_be_enabled()
            expect(page.locator("#testCaseEditor")).to_be_visible()
            expect(page.locator("#generatedTestCases")).to_contain_text(
                "社員と経費の関連を画面横断で確認する"
            )
            expect(page.locator("#testDataBadges")).to_contain_text("計画: 準備完了")
            expect(page.locator("#testDataBadges")).to_contain_text("実行: 合格")
            expect(page.locator("#testDataSummary")).to_contain_text("再利用データテンプレート")
            expect(page.locator("#testDataSummary")).to_contain_text("生成順序: employee → expense")
            expect(page.locator("#testDataSummary")).to_contain_text(
                "逆順クリーンアップ: expense → employee"
            )
            expect(page.locator("#testDataSummary")).to_contain_text("経費金額が 0 より大きい")
            expect(page.locator("#testDataFlows")).to_contain_text(
                "社員画面と経費画面を同一データ系列で検証する"
            )
            expect(page.locator("#testDataFlows")).to_contain_text(
                "画面操作 · 社員一覧 / 作成した社員を検索"
            )
            expect(page.locator("#testDataFlows")).to_contain_text(
                "画面操作 · 経費一覧 / 作成した経費を検索"
            )
            expect(page.locator("#testDataFlows")).to_contain_text(
                "入力変数（値は保存しません）"  # noqa: RUF001
            )
            expect(page.locator("#testDataFlows")).to_contain_text("employee_name")
            expect(page.locator("#testDataFlows")).to_contain_text("前提:")
            expect(page.locator("#testDataFlows")).to_contain_text("事後条件")
            expect(page.locator("#testDataFlows")).to_contain_text("最終業務アサーション")
            expect(page.locator("#testDataFlows")).to_contain_text(
                "クリーンアップ（Run 終了後に作成データを削除）"  # noqa: RUF001
            )
            image = page.locator("#screenshotGallery img")
            image.scroll_into_view_if_needed()
            expect(image).to_be_visible()
            expect(image).to_have_js_property("naturalWidth", 1280)
            expect(page.locator("#coverageSummary")).to_contain_text("100%")
            expect(page.locator("#coverageItems")).to_contain_text(
                "社員と経費の画面横断関連を確認する"
            )
            expect(page.locator("#closureSummary")).to_contain_text("テストケース別結果")
            expect(page.locator("#closureSummary")).to_contain_text(
                "必要な UI シナリオの検証結果がありません。"
            )
            expect(page.locator("#closureSummary")).to_contain_text("ブロック理由")
            expect(page.locator("#closureSummary")).to_contain_text(
                "UI 検証が未実行またはブロックされています。"
            )
            assert (
                "UI verification is blocked or missing"
                not in page.locator("#closureSummary").inner_text()
            )
            page.locator("#actor").fill("qa-user")
            page.locator("#testCaseInstruction").fill(
                "複数 Case の手順、テストデータ、期待結果、業務アサーションを一括変更"
            )
            page.locator("#modifyTestCase").click()
            expect(page.locator("#testCaseProposal")).to_contain_text(
                "全体差分: 2 Case · 確定変更 3 件 · 確認事項 1 件"
            )
            expect(page.locator("#testCaseProposal")).to_contain_text("社員検索結果を開く")
            expect(page.locator("#testCaseProposal")).to_contain_text("テストデータ項目")
            expect(page.locator("#testCaseProposal")).to_contain_text("業務アサーション")
            expect(page.locator("#generatedTestCases")).to_contain_text("社員一覧を開く")
            expect(page.locator("#generatedTestCases")).to_contain_text("合計 4 件を表示する")
            expect(page.locator("#testCaseAmbiguities")).to_contain_text(
                "全体適用前に確認が必要な選択"
            )
            page.locator('input[name="ambiguity-ambiguity-case"][value="option-change"]').check()
            page.get_by_role("button", name="全体差分を確認して新 Version を一度生成").click()
            expect(page.locator("#generatedTestCases")).to_contain_text("社員検索結果を開く")
            expect(page.locator("#generatedTestCases")).to_contain_text("合計 5 件を表示する")
            expect(page.locator("#testCaseRevisionHistory")).to_contain_text("一括修正 Version")
            expect(page.locator("#testCaseRevisionHistory")).to_contain_text(
                "失効 Run 1 件 · Evidence 1 件 · Closure 1 件"
            )
            expect(page.locator("#testCaseExecutionAuthorization")).to_contain_text(
                "Case 改訂後の実行承認"
            )
            expect(page.locator("#testCaseExecutionAuthorization")).to_contain_text("UI Scenario")
            expect(page.locator("#testCaseExecutionAuthorization")).to_contain_text("再確認が必要")
            expect(page.locator("#startTestData")).to_be_disabled()
            page.get_by_role("button", name="変更後の実行範囲を確認").click()
            expect(page.locator("#testCaseExecutionAuthorization")).to_contain_text("再確認済み")
            expect(page.locator("#startTestData")).to_be_enabled()
            expect(page.locator("#startTestData")).to_have_text(
                "この Case Version を新しい Run で再実行"
            )
            page.locator("#startTestData").click()
            expect(page.locator("#versionResultComparison")).to_contain_text("改訂前後の実行結果")
            expect(page.locator("#versionResultComparison")).to_contain_text("改訂前")
            expect(page.locator("#versionResultComparison")).to_contain_text("Evidence 1 件")
            page.get_by_role("button", name="この Version を取り消す").click()
            expect(page.locator("#generatedTestCases")).to_contain_text("社員一覧を開く")
            expect(page.locator("#generatedTestCases")).to_contain_text("合計 4 件を表示する")
            expect(page.locator("#testCaseRevisionHistory")).to_contain_text("取り消し Version")
            expect(page.locator("#testCaseRevisionHistory")).to_contain_text("取り消し済み")
            page.get_by_role("button", name="変更後の実行範囲を確認").click()
            expect(page.locator("#startTestData")).to_be_enabled()
            expect(page.locator("#startTestData")).to_have_text(
                "この Case Version を新しい Run で再実行"
            )
            page.locator("#startTestData").click()
            expect(page.locator("#versionResultComparison")).to_contain_text("改訂後")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert errors == []
