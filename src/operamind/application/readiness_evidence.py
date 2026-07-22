"""Generate repository readiness evidence from Canonical PostgreSQL facts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from operamind.infrastructure.postgres import ReadinessEvidenceInput
from operamind.readiness import MvpReadinessSummary, MvpReadinessValidator

AUTOMATED_GATE_ORDER = (
    "embedding_provider_live",
    "human_approval_e2e",
    "github_copilot_live",
    "target_deployment_e2e",
    "full_local_regression",
)
BLOCKING_REASON_BY_GATE = {
    "embedding_provider_live": (
        "No reviewed live provider probe matches a current ready Canonical Search Index."
    ),
    "human_approval_e2e": (
        "Canonical PostgreSQL has no confirmed Impact Report and non-revoked Approval "
        "Grant in an active or successfully completed edit chain for this case."
    ),
    "github_copilot_live": (
        "No reviewed VS Code GitHub Copilot receipt matches a committed, in-scope, "
        "command-verified Canonical Edit Result."
    ),
    "target_deployment_e2e": (
        "Canonical PostgreSQL has no passed, revision-bound, fully evidenced UI deployment run."
    ),
    "full_local_regression": (
        "No verified fixed-command regression result binds the current OperaMind source tree."
    ),
}


class ReadinessEvidenceSource(Protocol):
    def embedding_provider(self, project_id: str) -> ReadinessEvidenceInput | None: ...

    def human_approval(
        self, project_id: str, analysis_case_id: str
    ) -> ReadinessEvidenceInput | None: ...

    def copilot(self, project_id: str, analysis_case_id: str) -> ReadinessEvidenceInput | None: ...

    def deployment(
        self, project_id: str, analysis_case_id: str
    ) -> ReadinessEvidenceInput | None: ...

    def full_regression(self) -> ReadinessEvidenceInput | None: ...


@dataclass(frozen=True, slots=True)
class ReadinessSyncResult:
    changed: bool
    manifest_version: str
    summary: MvpReadinessSummary


class ReadinessEvidenceSyncService:
    """Materialize deterministic evidence, validate it, then atomically publish the manifest."""

    def __init__(self, *, repository_root: Path, source: ReadinessEvidenceSource) -> None:
        self._root = repository_root.resolve()
        self._source = source
        self._validator = MvpReadinessValidator(self._root)

    def sync(
        self,
        *,
        project_id: str,
        analysis_case_id: str,
        manifest_path: Path | None = None,
    ) -> ReadinessSyncResult:
        selected = (
            manifest_path.resolve()
            if manifest_path is not None
            else self._root / "readiness/mvp-readiness.json"
        )
        if not selected.is_relative_to(self._root):
            raise ValueError("Readiness manifest must stay within the repository")
        current = self._load_object(selected)
        raw_gates = current.get("gates")
        if not isinstance(raw_gates, list) or not all(isinstance(gate, dict) for gate in raw_gates):
            raise ValueError("Readiness manifest gates must be an array of objects")
        current_gates = {str(gate["gate_id"]): gate for gate in raw_gates}
        golden = current_gates.get("golden_dataset")
        if golden is None:
            raise ValueError("Readiness manifest has no golden_dataset gate")

        candidates = {
            "embedding_provider_live": self._source.embedding_provider(project_id),
            "human_approval_e2e": self._source.human_approval(project_id, analysis_case_id),
            "github_copilot_live": self._source.copilot(project_id, analysis_case_id),
            "target_deployment_e2e": self._source.deployment(project_id, analysis_case_id),
            "full_local_regression": self._source.full_regression(),
        }
        full = candidates["full_local_regression"]
        current_source_digest = self._validator.source_tree_digest(self._root)
        if full is not None and full.subject.get("source_tree_sha256") != current_source_digest:
            candidates["full_local_regression"] = None

        evidence_payloads: dict[str, tuple[Path, bytes, dict[str, object]]] = {}
        gates: list[dict[str, object]] = [dict(golden)]
        for gate_id in AUTOMATED_GATE_ORDER:
            candidate = candidates[gate_id]
            if candidate is None:
                gates.append(
                    {
                        "gate_id": gate_id,
                        "policy_version": "v1",
                        "status": "pending",
                        "blocking_reason": BLOCKING_REASON_BY_GATE[gate_id],
                        "evidence_refs": [],
                        "reviewers": [],
                    }
                )
                continue
            payload = self._evidence_payload(candidate)
            data = self._json_bytes(payload)
            content_digest = self._sha256(data)
            relative = (
                f"readiness/evidence/auto-{gate_id.replace('_', '-')}-{content_digest[:16]}.json"
            )
            path = self._root / relative
            evidence_payloads[gate_id] = (path, data, payload)
            gates.append(
                {
                    "gate_id": gate_id,
                    "policy_version": "v1",
                    "status": "passed",
                    "evidence_refs": [
                        {
                            "evidence_id": candidate.evidence_id,
                            "evidence_type": candidate.evidence_type,
                            "path": relative,
                            "sha256": content_digest,
                            "observed_at": self._isoformat(candidate),
                        }
                    ],
                    "reviewers": list(candidate.reviewed_by),
                }
            )

        comparable = {
            "manifest_id": current["manifest_id"],
            "status": "ready" if all(gate["status"] == "passed" for gate in gates) else "pending",
            "gates": gates,
        }
        current_comparable = {key: current[key] for key in comparable}
        changed = comparable != current_comparable
        version = str(current["manifest_version"])
        if changed:
            version = self._increment_patch(version)
        candidate_manifest = {
            "manifest_id": current["manifest_id"],
            "manifest_version": version,
            "status": comparable["status"],
            "gates": gates,
        }

        for path, data, _ in evidence_payloads.values():
            self._atomic_write_if_changed(path, data)
            report = self._validator.validate_reviewed_evidence(path)
            if not report.is_valid:
                raise ValueError(self._format_issues(report.issues))

        manifest_data = self._json_bytes(candidate_manifest)
        temporary = selected.with_name(f".{selected.name}.sync.tmp")
        temporary.write_bytes(manifest_data)
        try:
            report = self._validator.validate(temporary)
            if not report.is_valid:
                raise ValueError(self._format_issues(report.issues))
            if changed or selected.read_bytes() != manifest_data:
                os.replace(temporary, selected)
            else:
                temporary.unlink()
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

        return ReadinessSyncResult(
            changed=changed,
            manifest_version=version,
            summary=self._validator.summarize(selected),
        )

    @staticmethod
    def _evidence_payload(candidate: ReadinessEvidenceInput) -> dict[str, object]:
        return {
            "evidence_format_version": "v1",
            "evidence_id": candidate.evidence_id,
            "gate_id": candidate.gate_id,
            "evidence_type": candidate.evidence_type,
            "outcome": "passed",
            "observed_at": ReadinessEvidenceSyncService._isoformat(candidate),
            "review_status": candidate.review_status,
            "reviewed_by": list(candidate.reviewed_by),
            "subject": candidate.subject,
        }

    @staticmethod
    def _isoformat(candidate: ReadinessEvidenceInput) -> str:
        return candidate.observed_at.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _json_bytes(payload: object) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()

    @staticmethod
    def _sha256(data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _increment_patch(version: str) -> str:
        core = version.split("-", 1)[0]
        major, minor, patch = (int(value) for value in core.split("."))
        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _load_object(path: Path) -> dict[str, object]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Expected a JSON object: {path}")
        return payload

    @staticmethod
    def _atomic_write_if_changed(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            if path.read_bytes() == data:
                return
            raise ValueError(f"Content-addressed evidence path collision: {path}")
        temporary = path.with_name(f".{path.name}.sync.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    @staticmethod
    def _format_issues(issues: tuple[object, ...]) -> str:
        return "; ".join(str(issue) for issue in issues)
