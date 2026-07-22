"""Immutable, approved browser execution manifests bound to one UI Plan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection

from operamind.domain import BrowserExecutionManifest, UiKnowledgeSnapshot
from operamind.infrastructure.postgres.errors import PersistenceConflictError
from operamind.infrastructure.postgres.ui_knowledge_repository import UiKnowledgeRepository
from operamind.infrastructure.postgres.ui_verification_repository import UI_EVIDENCE_TYPES


@dataclass(frozen=True, slots=True)
class BrowserManifestRecord:
    created: bool
    manifest_id: str
    review_status: str


@dataclass(frozen=True, slots=True)
class ApprovedBrowserManifest:
    manifest: BrowserExecutionManifest
    base_url: str
    scenario_evidence_requirements: tuple[tuple[str, tuple[str, ...]], ...]


class UiBrowserManifestRepository:
    """Freeze a reviewed declarative DSL against exact Plan Scenario versions."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def store(self, manifest: BrowserExecutionManifest) -> BrowserManifestRecord:
        canonical = _json(manifest.to_dict())
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload_digest, review_status FROM ui_browser_manifests
                WHERE browser_manifest_id = %s
                """,
                (manifest.manifest_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if tuple(existing) != (digest, manifest.review_status):
                    raise PersistenceConflictError(
                        f"Browser Manifest identity has different content: {manifest.manifest_id}"
                    )
                _assert_manifest_normalized(cursor, manifest=manifest, digest=digest)
                return BrowserManifestRecord(False, manifest.manifest_id, manifest.review_status)
            cursor.execute(
                """
                SELECT plan.status, environment.base_url, packet.allowed_items,
                       plan.environment_id, plan.deployment_revision,
                       plan.repository_binding_status, environment.status,
                       deployment.status, plan.repository_revision,
                       deployment.repository_revision, packet.test_files
                FROM ui_execution_plans AS plan
                JOIN ui_environments AS environment
                  ON environment.environment_id = plan.environment_id
                 AND environment.project_id = plan.project_id
                JOIN ui_deployments AS deployment
                  ON deployment.environment_id = plan.environment_id
                 AND deployment.project_id = plan.project_id
                 AND deployment.deployment_revision = plan.deployment_revision
                JOIN edit_packets AS packet
                  ON packet.edit_packet_id = plan.edit_packet_id
                 AND packet.project_id = plan.project_id
                WHERE plan.ui_execution_plan_id = %s AND plan.project_id = %s
                FOR UPDATE OF plan
                FOR SHARE OF environment, deployment, packet
                """,
                (manifest.plan_id, manifest.project_id),
            )
            plan = cursor.fetchone()
            if plan is None:
                raise ValueError("Browser Manifest Plan does not exist in requested project")
            if str(plan[0]) not in {"preflight_pending", "ready", "blocked"}:
                raise ValueError("Browser Manifest Plan is already completed")
            if tuple(plan[5:8]) != ("verified", "active", "ready") or str(plan[8]) != str(plan[9]):
                raise ValueError("Browser Manifest Plan source is no longer deployable")
            cursor.execute(
                """
                SELECT planned.scenario_id, planned.scenario_version_id,
                       scenario.trigger_path, scenario.data_recipe_ref
                FROM ui_execution_plan_scenarios AS planned
                JOIN verification_scenarios AS scenario
                  ON scenario.scenario_version_id = planned.scenario_version_id
                 AND scenario.project_id = planned.project_id
                WHERE planned.ui_execution_plan_id = %s AND planned.project_id = %s
                ORDER BY planned.execution_order
                """,
                (manifest.plan_id, manifest.project_id),
            )
            planned = tuple(
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    str(row[3]) if row[3] is not None else None,
                )
                for row in cursor.fetchall()
            )
            by_id = {scenario.scenario_id: scenario for scenario in manifest.scenarios}
            planned_ids = tuple(row[0] for row in planned)
            manifest_ids = tuple(scenario.scenario_id for scenario in manifest.scenarios)
            if manifest_ids != planned_ids or len(by_id) != len(manifest.scenarios):
                raise ValueError(
                    "Browser Manifest must define every planned Scenario once in execution order"
                )
            for scenario_id, _, trigger_path, data_recipe_ref in planned:
                if by_id[scenario_id].trigger_path != trigger_path:
                    raise ValueError(
                        f"Browser Manifest trigger_path differs from Scenario: {scenario_id}"
                    )
                if data_recipe_ref is not None and not any(
                    assertion.failure_category.value == "test_data"
                    for assertion in by_id[scenario_id].preflight_assertions
                ):
                    raise ValueError(
                        f"Scenario with data_recipe_ref requires test_data Preflight: {scenario_id}"
                    )
            packet_items = _impact_item_ids(
                cast(list[object], plan[2]),
                test_files={str(value) for value in cast(list[object], plan[10])},
            )
            manifest_items = {
                item_id for scenario in manifest.scenarios for item_id in scenario.impact_item_refs
            }
            if manifest_items != packet_items:
                missing = sorted(packet_items - manifest_items)
                unexpected = sorted(manifest_items - packet_items)
                raise ValueError(
                    "Browser Manifest must cover every Packet Impact Item exactly "
                    f"(business items only); missing={missing}, unexpected={unexpected}"
                )
            if manifest.ui_knowledge_snapshot_id is not None:
                UiKnowledgeRepository(self._connection).load_approved(
                    project_id=manifest.project_id,
                    snapshot_id=manifest.ui_knowledge_snapshot_id,
                    environment_id=str(plan[3]),
                    deployment_revision=str(plan[4]),
                )
            cursor.execute(
                """
                INSERT INTO ui_browser_manifests (
                    browser_manifest_id, ui_execution_plan_id, project_id,
                    browser_name, browser_channel, headless, viewport_width,
                    viewport_height, review_status, reviewed_by, payload_digest,
                    ui_knowledge_snapshot_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    manifest.manifest_id,
                    manifest.plan_id,
                    manifest.project_id,
                    manifest.browser_name,
                    manifest.browser_channel,
                    manifest.headless,
                    manifest.viewport_width,
                    manifest.viewport_height,
                    manifest.review_status,
                    manifest.reviewed_by,
                    digest,
                    manifest.ui_knowledge_snapshot_id,
                ),
            )
            version_by_id = {row[0]: row[1] for row in planned}
            for scenario in manifest.scenarios:
                cursor.execute(
                    """
                    INSERT INTO ui_browser_scenario_specs (
                        browser_manifest_id, project_id, ui_execution_plan_id,
                        scenario_id, scenario_version_id, trigger_path,
                        impact_item_refs, actions, assertions, redaction_locators,
                        preflight_assertions
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s::jsonb
                    )
                    """,
                    (
                        manifest.manifest_id,
                        manifest.project_id,
                        manifest.plan_id,
                        scenario.scenario_id,
                        version_by_id[scenario.scenario_id],
                        scenario.trigger_path,
                        _json(list(scenario.impact_item_refs)),
                        _json([item.to_dict() for item in scenario.actions]),
                        _json([item.to_dict() for item in scenario.assertions]),
                        _json([item.to_dict() for item in scenario.redaction_locators]),
                        _json([item.to_dict() for item in scenario.preflight_assertions]),
                    ),
                )
        return BrowserManifestRecord(True, manifest.manifest_id, manifest.review_status)

    def load_approved(self, *, project_id: str, plan_id: str) -> ApprovedBrowserManifest:
        return self._load_approved(
            project_id=project_id,
            plan_id=plan_id,
            allowed_plan_statuses={"ready"},
        )

    def load_for_preflight(self, *, project_id: str, plan_id: str) -> ApprovedBrowserManifest:
        return self._load_approved(
            project_id=project_id,
            plan_id=plan_id,
            allowed_plan_statuses={"preflight_pending", "blocked"},
        )

    def _load_approved(
        self,
        *,
        project_id: str,
        plan_id: str,
        allowed_plan_statuses: set[str],
    ) -> ApprovedBrowserManifest:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT manifest.browser_manifest_id, manifest.browser_name,
                       manifest.browser_channel, manifest.headless,
                       manifest.viewport_width, manifest.viewport_height,
                       manifest.review_status, manifest.reviewed_by,
                       manifest.payload_digest, environment.base_url, plan.status,
                       manifest.ui_knowledge_snapshot_id,
                       plan.environment_id, plan.deployment_revision,
                       plan.repository_binding_status, environment.status,
                       deployment.status, deployment.repository_revision,
                       plan.repository_revision
                FROM ui_browser_manifests AS manifest
                JOIN ui_execution_plans AS plan
                  ON plan.ui_execution_plan_id = manifest.ui_execution_plan_id
                 AND plan.project_id = manifest.project_id
                JOIN ui_environments AS environment
                  ON environment.environment_id = plan.environment_id
                 AND environment.project_id = plan.project_id
                JOIN ui_deployments AS deployment
                  ON deployment.environment_id = plan.environment_id
                 AND deployment.project_id = plan.project_id
                 AND deployment.deployment_revision = plan.deployment_revision
                WHERE manifest.project_id = %s
                  AND manifest.ui_execution_plan_id = %s
                  AND manifest.review_status = 'approved'
                """,
                (project_id, plan_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("UI Plan has no approved Browser Manifest")
            if str(row[10]) not in allowed_plan_statuses:
                raise ValueError("Approved Browser Manifest cannot be used in current Plan state")
            if tuple(row[14:17]) != ("verified", "active", "ready") or str(row[17]) != str(row[18]):
                raise ValueError("Approved Browser Manifest source is no longer deployable")
            cursor.execute(
                """
                SELECT spec.scenario_id, spec.trigger_path, spec.impact_item_refs,
                       spec.actions, spec.assertions, spec.redaction_locators,
                       spec.preflight_assertions, scenario.evidence_requirements
                FROM ui_browser_scenario_specs AS spec
                JOIN ui_execution_plan_scenarios AS planned
                  ON planned.ui_execution_plan_id = spec.ui_execution_plan_id
                 AND planned.project_id = spec.project_id
                 AND planned.scenario_id = spec.scenario_id
                 AND planned.scenario_version_id = spec.scenario_version_id
                JOIN verification_scenarios AS scenario
                  ON scenario.scenario_version_id = planned.scenario_version_id
                 AND scenario.project_id = planned.project_id
                WHERE spec.browser_manifest_id = %s AND spec.project_id = %s
                ORDER BY planned.execution_order
                """,
                (row[0], project_id),
            )
            scenario_rows = cursor.fetchall()
            scenarios = [
                {
                    "scenario_id": str(item[0]),
                    "trigger_path": str(item[1]),
                    "impact_item_refs": cast(list[object], item[2]),
                    "actions": cast(list[object], item[3]),
                    "assertions": cast(list[object], item[4]),
                    "redaction_locators": cast(list[object], item[5]),
                    "preflight_assertions": cast(list[object], item[6]),
                }
                for item in scenario_rows
            ]
            scenario_evidence_requirements = tuple(
                (
                    str(item[0]),
                    tuple(str(value) for value in cast(list[object], item[7])),
                )
                for item in scenario_rows
            )
        manifest = BrowserExecutionManifest.from_dict(
            {
                "manifest_id": str(row[0]),
                "plan_id": plan_id,
                "project_id": project_id,
                "browser": {
                    "name": str(row[1]),
                    "channel": str(row[2]) if row[2] is not None else None,
                    "headless": bool(row[3]),
                    "viewport": {"width": int(row[4]), "height": int(row[5])},
                },
                "review_status": str(row[6]),
                "reviewed_by": str(row[7]) if row[7] is not None else None,
                "ui_knowledge_snapshot_id": str(row[11]) if row[11] is not None else None,
                "scenarios": scenarios,
            }
        )
        normalized_digest = hashlib.sha256(_json(manifest.to_dict()).encode()).hexdigest()
        if normalized_digest != str(row[8]):
            raise PersistenceConflictError(
                f"Browser Manifest normalized identity differs: {manifest.manifest_id}"
            )
        if manifest.ui_knowledge_snapshot_id is not None:
            knowledge = UiKnowledgeRepository(self._connection).load_approved(
                project_id=project_id,
                snapshot_id=manifest.ui_knowledge_snapshot_id,
                environment_id=str(row[12]),
                deployment_revision=str(row[13]),
            )
            manifest = _resolve_business_targets(manifest, knowledge)
        requirement_ids = tuple(value[0] for value in scenario_evidence_requirements)
        manifest_ids = tuple(value.scenario_id for value in manifest.scenarios)
        if requirement_ids != manifest_ids or any(
            not requirements
            or len(requirements) != len(set(requirements))
            or not set(requirements).issubset(UI_EVIDENCE_TYPES)
            for _, requirements in scenario_evidence_requirements
        ):
            raise RuntimeError(
                "Approved Browser Manifest Scenario Evidence requirements are not normalized"
            )
        return ApprovedBrowserManifest(
            manifest=manifest,
            base_url=str(row[9]),
            scenario_evidence_requirements=scenario_evidence_requirements,
        )


def _assert_manifest_normalized(
    cursor: Any,
    *,
    manifest: BrowserExecutionManifest,
    digest: str,
) -> None:
    cursor.execute(
        """
        SELECT ui_execution_plan_id, project_id, browser_name, browser_channel,
               headless, viewport_width, viewport_height, review_status,
               reviewed_by, payload_digest, ui_knowledge_snapshot_id
        FROM ui_browser_manifests
        WHERE browser_manifest_id = %s
        """,
        (manifest.manifest_id,),
    )
    header = cursor.fetchone()
    expected_header = (
        manifest.plan_id,
        manifest.project_id,
        manifest.browser_name,
        manifest.browser_channel,
        manifest.headless,
        manifest.viewport_width,
        manifest.viewport_height,
        manifest.review_status,
        manifest.reviewed_by,
        digest,
        manifest.ui_knowledge_snapshot_id,
    )
    if header is None or tuple(header) != expected_header:
        raise PersistenceConflictError(
            f"Browser Manifest normalized identity differs: {manifest.manifest_id}"
        )
    cursor.execute(
        """
        SELECT spec.scenario_id, spec.trigger_path, spec.impact_item_refs,
               spec.actions, spec.assertions, spec.redaction_locators,
               spec.preflight_assertions
        FROM ui_browser_scenario_specs AS spec
        JOIN ui_execution_plan_scenarios AS planned
          ON planned.ui_execution_plan_id = spec.ui_execution_plan_id
         AND planned.project_id = spec.project_id
         AND planned.scenario_id = spec.scenario_id
         AND planned.scenario_version_id = spec.scenario_version_id
        WHERE spec.browser_manifest_id = %s AND spec.project_id = %s
        ORDER BY planned.execution_order
        """,
        (manifest.manifest_id, manifest.project_id),
    )
    actual_specs = tuple(tuple(row) for row in cursor.fetchall())
    expected_specs = tuple(
        (
            scenario.scenario_id,
            scenario.trigger_path,
            list(scenario.impact_item_refs),
            [item.to_dict() for item in scenario.actions],
            [item.to_dict() for item in scenario.assertions],
            [item.to_dict() for item in scenario.redaction_locators],
            [item.to_dict() for item in scenario.preflight_assertions],
        )
        for scenario in manifest.scenarios
    )
    if actual_specs != expected_specs:
        raise PersistenceConflictError(
            f"Browser Manifest normalized Scenario content differs: {manifest.manifest_id}"
        )


def _resolve_business_targets(
    manifest: BrowserExecutionManifest,
    knowledge: UiKnowledgeSnapshot,
) -> BrowserExecutionManifest:
    payload = manifest.to_dict()
    scenarios = cast(list[object], payload["scenarios"])
    for raw_scenario in scenarios:
        scenario = cast(dict[str, object], raw_scenario)
        for collection_name in ("actions", "assertions"):
            collection = cast(list[object], scenario[collection_name])
            for raw_item in collection:
                item = cast(dict[str, object], raw_item)
                item["locator"] = _resolve_locator(item["locator"], knowledge)
        preflight = cast(list[object], scenario["preflight_assertions"])
        for raw_item in preflight:
            item = cast(dict[str, object], raw_item)
            item["locator"] = _resolve_locator(item["locator"], knowledge)
        redactions = cast(list[object], scenario["redaction_locators"])
        scenario["redaction_locators"] = [_resolve_locator(item, knowledge) for item in redactions]
    return BrowserExecutionManifest.from_dict(payload)


def _resolve_locator(raw: object, knowledge: UiKnowledgeSnapshot) -> dict[str, object]:
    value = cast(dict[str, object], raw)
    target_ref = value.get("target_ref")
    if target_ref is None:
        return value
    if not isinstance(target_ref, str):
        raise RuntimeError("Browser Manifest contains an invalid business target reference")
    return knowledge.resolve(target_ref).to_dict()


def _impact_item_ids(values: list[object], *, test_files: set[str]) -> set[str]:
    result: set[str] = set()
    seen: set[str] = set()
    for raw in values:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("impact_item_id"), str)
            or not isinstance(raw.get("target_path"), str)
        ):
            raise RuntimeError("Edit Packet allowed_items are not normalized")
        item_id = cast(str, raw["impact_item_id"])
        target_path = cast(str, raw["target_path"])
        if not item_id.strip() or not target_path.strip() or item_id in seen:
            raise RuntimeError("Edit Packet Impact Item IDs must be unique and non-blank")
        seen.add(item_id)
        if target_path in test_files:
            continue
        result.add(item_id)
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
