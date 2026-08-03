"""Fail-closed business-requirement coverage for generated verification plans."""

from __future__ import annotations

from typing import Any, cast

_EVIDENCE_KINDS = {
    "code_test",
    "command_evidence",
    "canonical_evidence",
    "plan_evidence",
}


def assess_planned_business_coverage(
    *,
    request: dict[str, Any],
    test_plan: dict[str, Any],
    test_data_plan: dict[str, Any],
    scoped_test_files: frozenset[str],
    passed_command_refs: frozenset[str],
    canonical_artifact_refs: frozenset[str],
    required_ui_scenario_refs: tuple[str, ...],
) -> dict[str, Any]:
    """Calculate coverage only from rule-bound executable UI cases.

    Other current-scope Evidence remains visible for audit, but it cannot replace
    a runnable UI case and therefore cannot raise the business coverage result.
    """

    rules = cast(list[dict[str, Any]], request.get("business_rules", []))
    rule_ids = {str(rule.get("business_rule_id") or "") for rule in rules}
    if not rule_ids or "" in rule_ids:
        raise ValueError("Change Request business rules must have non-empty identities")
    if (
        len(required_ui_scenario_refs) > len(rules)
        or len(required_ui_scenario_refs) != len(set(required_ui_scenario_refs))
        or any(not scenario_id.strip() for scenario_id in required_ui_scenario_refs)
    ):
        raise ValueError(
            "Required UI scenarios must be unique non-empty positional bindings"
        )
    expected_rule_by_case = {
        scenario_id: str(rule["business_rule_id"])
        for scenario_id, rule in zip(required_ui_scenario_refs, rules, strict=False)
    }

    cases = cast(list[dict[str, Any]], test_plan.get("test_cases", []))
    executable_case_ids = _executable_ui_case_ids(
        cases=cases,
        test_data_plan=test_data_plan,
    )
    sources_by_rule: dict[str, list[dict[str, Any]]] = {rule_id: [] for rule_id in rule_ids}
    case_refs_by_rule: dict[str, set[str]] = {rule_id: set() for rule_id in rule_ids}
    criterion_refs_by_rule: dict[str, set[str]] = {rule_id: set() for rule_id in rule_ids}

    for case in cases:
        case_id = str(case.get("test_case_id") or "")
        if case_id not in executable_case_ids:
            continue
        declared_rule_ids = {
            str(value) for value in cast(list[object], case.get("business_rule_refs", []))
        }
        expected_rule_id = expected_rule_by_case.get(case_id)
        if expected_rule_id is None:
            raise ValueError(f"UI TestPlan has an unbound executable scenario: {case_id}")
        if declared_rule_ids != {expected_rule_id}:
            raise ValueError(
                "UI TestPlan scenario must cover only its Impact-bound business rule: "
                f"scenario_id={case_id}; expected={expected_rule_id}; "
                f"actual={sorted(declared_rule_ids)}"
            )
        for rule_id in declared_rule_ids:
            if rule_id not in rule_ids:
                raise ValueError(f"UI TestPlan references unknown business rule: {rule_id}")
            criteria = sorted(
                str(value) for value in cast(list[object], case.get("acceptance_criteria_refs", []))
            )
            if not criteria:
                raise ValueError(f"UI TestPlan case has no acceptance criteria: {case_id}")
            case_refs_by_rule[rule_id].add(case_id)
            criterion_refs_by_rule[rule_id].update(criteria)
            sources_by_rule[rule_id].append(
                {
                    "source_kind": "ui_test",
                    "source_refs": [case_id, *criteria],
                    "assertion": " / ".join(
                        str(value) for value in cast(list[object], case.get("expected_results", []))
                    ),
                }
            )

    available_plan_components = _available_plan_components(test_plan, test_data_plan)
    for claim in cast(list[dict[str, Any]], test_plan.get("requirement_evidence", [])):
        rule_id = str(claim.get("business_rule_id") or "")
        if rule_id not in rule_ids:
            raise ValueError(f"Requirement Evidence references unknown business rule: {rule_id}")
        kind = str(claim.get("verification_kind") or "")
        if kind not in _EVIDENCE_KINDS:
            raise ValueError(f"Unsupported Requirement Evidence kind: {kind or '<blank>'}")
        assertion = str(claim.get("assertion") or "").strip()
        if not assertion:
            raise ValueError(f"Requirement Evidence has no verification assertion: {rule_id}")
        test_files = _refs(claim, "test_file_refs")
        commands = _refs(claim, "command_refs")
        artifacts = _refs(claim, "artifact_refs")
        components = _refs(claim, "plan_component_refs")
        _validate_claim_sources(
            rule_id=rule_id,
            kind=kind,
            test_files=test_files,
            commands=commands,
            artifacts=artifacts,
            components=components,
            scoped_test_files=scoped_test_files,
            passed_command_refs=passed_command_refs,
            canonical_artifact_refs=canonical_artifact_refs,
            available_plan_components=available_plan_components,
        )
        refs = sorted(test_files | commands | artifacts | components)
        sources_by_rule[rule_id].append(
            {"source_kind": kind, "source_refs": refs, "assertion": assertion}
        )

    items = [
        {
            "business_rule_id": str(rule["business_rule_id"]),
            "test_case_refs": sorted(case_refs_by_rule[str(rule["business_rule_id"])]),
            "criterion_refs": sorted(criterion_refs_by_rule[str(rule["business_rule_id"])]),
            "verification_sources": sources_by_rule[str(rule["business_rule_id"])],
            "status": (
                "covered" if case_refs_by_rule[str(rule["business_rule_id"])] else "uncovered"
            ),
        }
        for rule in rules
    ]
    covered = sum(item["status"] == "covered" for item in items)
    total = len(items)
    return {
        "business_rule_count": total,
        "covered_rule_count": covered,
        "coverage_percent": covered * 100 / total,
        "items": items,
        "status": "passed" if covered == total else "failed",
    }


def uncovered_business_rules(
    *, request: dict[str, Any], assessment: dict[str, Any]
) -> list[dict[str, str]]:
    texts = {
        str(rule["business_rule_id"]): str(rule.get("text") or "")
        for rule in cast(list[dict[str, Any]], request.get("business_rules", []))
    }
    return [
        {
            "business_rule_id": str(item["business_rule_id"]),
            "text": texts.get(str(item["business_rule_id"]), ""),
        }
        for item in cast(list[dict[str, Any]], assessment.get("items", []))
        if item.get("status") == "uncovered"
    ]


def canonical_artifact_refs_from_output(output_refs: dict[str, Any]) -> frozenset[str]:
    """Return immutable pre-planning Artifact refs that Copilot may cite."""

    refs = {
        str(output_refs[key])
        for key in (
            "source_document_snapshot_id",
            "target_document_snapshot_id",
            "search_index_build_id",
            "impact_report_id",
        )
        if isinstance(output_refs.get(key), str) and str(output_refs[key]).strip()
    }
    refs.update(
        str(value)
        for value in cast(list[object], output_refs.get("document_change_refs", []))
        if str(value).strip()
    )
    return frozenset(refs)


def _executable_ui_case_ids(
    *, cases: list[dict[str, Any]], test_data_plan: dict[str, Any]
) -> set[str]:
    data_ids = {
        str(item.get("test_data_id") or "")
        for item in cast(list[dict[str, Any]], test_data_plan.get("data_sets", []))
    }
    flows = cast(list[dict[str, Any]], test_data_plan.get("generation_flows", []))
    required_steps_by_case = {
        str(case.get("test_case_id") or ""): _refs(case, "step_ids") for case in cases
    }
    mapped_steps_by_case: dict[str, set[str]] = {}
    asserted_steps_by_case: dict[str, set[str]] = {}
    flow_data_by_case: dict[str, set[str]] = {}
    for flow in flows:
        case_refs = {str(value) for value in cast(list[object], flow.get("test_case_refs", []))}
        flow_data = {str(value) for value in cast(list[object], flow.get("test_data_refs", []))}
        steps = cast(list[dict[str, Any]], flow.get("steps", []))
        for case_id in case_refs:
            case_steps = required_steps_by_case.get(case_id, set())
            for step in steps:
                if step.get("channel") != "ui" or not isinstance(step.get("playwright"), dict):
                    continue
                playwright = cast(dict[str, Any], step["playwright"])
                if not str(playwright.get("action") or "").strip():
                    continue
                mapped_refs = _refs(step, "test_step_refs") & case_steps
                mapped_steps_by_case.setdefault(case_id, set()).update(mapped_refs)
                if cast(list[object], playwright.get("observations", [])) and cast(
                    list[object], step.get("postconditions", [])
                ):
                    asserted_steps_by_case.setdefault(case_id, set()).update(mapped_refs)
            flow_data_by_case.setdefault(case_id, set()).update(flow_data)
    executable: set[str] = set()
    for case in cases:
        case_id = str(case.get("test_case_id") or "")
        case_data = _refs(case, "test_data_refs")
        step_ids = _refs(case, "step_ids")
        if (
            case_id
            and case.get("level") == "ui"
            and case.get("execution_mode") == "browser"
            and case_id in mapped_steps_by_case
            and case_data
            and case_data.issubset(data_ids)
            and case_data.issubset(flow_data_by_case.get(case_id, set()))
            and step_ids
            and step_ids.issubset(mapped_steps_by_case[case_id])
            and step_ids.issubset(asserted_steps_by_case.get(case_id, set()))
            and cast(list[object], case.get("expected_results", []))
        ):
            executable.add(case_id)
    return executable


def _available_plan_components(
    test_plan: dict[str, Any], test_data_plan: dict[str, Any]
) -> frozenset[str]:
    components = {"ui_test_plan", "test_data_plan"}
    flows = cast(list[dict[str, Any]], test_data_plan.get("generation_flows", []))
    if flows:
        components.add("generation_flows")
    if any(cast(list[object], flow.get("cleanup_steps", [])) for flow in flows):
        components.add("cleanup")
    if any(
        cast(list[object], playwright.get("observations", []))
        for flow in flows
        for step in cast(list[dict[str, Any]], flow.get("steps", []))
        if (playwright := cast(dict[str, Any], step.get("playwright") or {}))
    ):
        components.add("playwright_observations")
    return frozenset(components)


def _validate_claim_sources(
    *,
    rule_id: str,
    kind: str,
    test_files: set[str],
    commands: set[str],
    artifacts: set[str],
    components: set[str],
    scoped_test_files: frozenset[str],
    passed_command_refs: frozenset[str],
    canonical_artifact_refs: frozenset[str],
    available_plan_components: frozenset[str],
) -> None:
    if kind == "code_test":
        valid = (
            bool(test_files)
            and bool(commands)
            and test_files.issubset(scoped_test_files)
            and commands.issubset(passed_command_refs)
            and not artifacts
            and not components
        )
    elif kind == "command_evidence":
        valid = (
            bool(commands)
            and commands.issubset(passed_command_refs)
            and not test_files
            and not artifacts
            and not components
        )
    elif kind == "canonical_evidence":
        valid = (
            bool(artifacts)
            and artifacts.issubset(canonical_artifact_refs)
            and not test_files
            and not commands
            and not components
        )
    else:
        valid = (
            bool(components)
            and components.issubset(available_plan_components)
            and not test_files
            and not commands
            and not artifacts
        )
    if not valid:
        raise ValueError(
            "Requirement Evidence is not backed by allowed current-scope sources: "
            f"business_rule_id={rule_id}, verification_kind={kind}"
        )


def _refs(value: dict[str, Any], key: str) -> set[str]:
    refs = {str(item) for item in cast(list[object], value.get(key, []))}
    if "" in refs:
        raise ValueError(f"Requirement Evidence contains a blank {key} value")
    return refs
