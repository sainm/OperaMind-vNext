"""Local pause/resume checkpoints for quota-limited VS Code GitHub Copilot work."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from operamind.infrastructure.code_graph import GitDiffEvidence, GitWorktreeDiffInspector


@dataclass(frozen=True, slots=True)
class CopilotCheckpointRequest:
    session_id: str
    phase: str
    project_id: str
    analysis_case_id: str
    registered_repository_root: Path
    workspace_root: Path
    base_revision: str
    expected_outputs: tuple[str, ...]
    approval_grant_id: str | None = None
    edit_packet_id: str | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.session_id, self.project_id, self.analysis_case_id)
        ):
            raise ValueError("Copilot checkpoint identity fields must not be blank")
        if self.phase not in {"draft_generation", "code_edit"}:
            raise ValueError("Copilot checkpoint phase must be draft_generation or code_edit")
        if re.fullmatch(r"[0-9a-f]{40}", self.base_revision) is None:
            raise ValueError("Copilot checkpoint requires a full base revision SHA")
        if not self.expected_outputs or any(not value.strip() for value in self.expected_outputs):
            raise ValueError("Copilot checkpoint expected outputs must not be blank")
        if self.phase == "code_edit" and (
            not self.approval_grant_id or not self.edit_packet_id
        ):
            raise ValueError("Code-edit checkpoint requires Approval Grant and Edit Packet IDs")


class CopilotCheckpointService:
    """Persist and revalidate resumable local Copilot work without invoking a model."""

    _FILENAME = "copilot-checkpoint.json"
    _REHEARSAL_FILENAME = "codex-implementation-rehearsal.json"
    _REHEARSAL_INSTRUCTIONS_FILENAME = "COPILOT-REHEARSAL-INSTRUCTIONS.md"

    def __init__(self) -> None:
        self._git = GitWorktreeDiffInspector()

    def initialize(
        self,
        *,
        checkpoint_root: Path,
        request: CopilotCheckpointRequest,
    ) -> dict[str, Any]:
        root = checkpoint_root.absolute()
        path = root / self._FILENAME
        if path.exists():
            raise FileExistsError(f"Copilot checkpoint already exists: {path}")
        self._validate_workspace(request)
        if request.phase == "code_edit" and self._inside_target_repository(root, request):
            raise ValueError("Code-edit checkpoint files must stay outside target repositories")
        now = datetime.now(UTC).isoformat()
        payload: dict[str, Any] = {
            "schema_version": "v1",
            "session_id": request.session_id,
            "phase": request.phase,
            "status": "active",
            "project_id": request.project_id,
            "analysis_case_id": request.analysis_case_id,
            "registered_repository_root": str(
                request.registered_repository_root.resolve(strict=True)
            ),
            "workspace_root": str(request.workspace_root.resolve(strict=True)),
            "base_revision": request.base_revision,
            "approval_grant_id": request.approval_grant_id,
            "edit_packet_id": request.edit_packet_id,
            "expected_outputs": list(request.expected_outputs),
            "pause_reason": None,
            "resume_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        root.mkdir(parents=True, exist_ok=True)
        return self._write(path, payload)

    def load(self, checkpoint_root: Path) -> dict[str, Any]:
        path = checkpoint_root.resolve(strict=True) / self._FILENAME
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Copilot checkpoint must be a JSON object")
        payload = cast(dict[str, Any], value)
        self._validate_payload(payload)
        return payload

    def pause(self, *, checkpoint_root: Path, reason: str) -> dict[str, Any]:
        if reason not in {"free_quota_exhausted", "model_capacity", "user_requested"}:
            raise ValueError("Unsupported Copilot pause reason")
        payload = self.load(checkpoint_root)
        if payload["status"] != "active":
            raise ValueError(f"Copilot checkpoint cannot pause from status={payload['status']}")
        self._validate_workspace(self._request(payload))
        payload["status"] = "paused"
        payload["pause_reason"] = reason
        payload["updated_at"] = datetime.now(UTC).isoformat()
        return self._write(checkpoint_root.resolve() / self._FILENAME, payload)

    def resume(self, *, checkpoint_root: Path) -> dict[str, Any]:
        payload = self.load(checkpoint_root)
        if payload["status"] != "paused":
            raise ValueError(f"Copilot checkpoint cannot resume from status={payload['status']}")
        rehearsal_path = checkpoint_root.resolve(strict=True) / self._REHEARSAL_FILENAME
        if rehearsal_path.exists():
            rehearsal = self.load_rehearsal(checkpoint_root)["proposal"]
            if rehearsal["status"] == "needs_reanalysis":
                raise ValueError("Copilot checkpoint requires reanalysis before resume")
        self._validate_workspace(self._request(payload))
        payload["status"] = "active"
        payload["pause_reason"] = None
        payload["resume_count"] = int(payload["resume_count"]) + 1
        payload["updated_at"] = datetime.now(UTC).isoformat()
        return self._write(checkpoint_root.resolve() / self._FILENAME, payload)

    def rebind_grant(
        self,
        *,
        checkpoint_root: Path,
        expected_previous_grant_id: str,
        approval_grant_id: str,
    ) -> dict[str, Any]:
        """Replace only an expired/revoked Grant while a code-edit checkpoint is paused."""

        if not expected_previous_grant_id.strip() or not approval_grant_id.strip():
            raise ValueError("Copilot checkpoint Grant IDs must not be blank")
        payload = self.load(checkpoint_root)
        if payload["phase"] != "code_edit" or payload["status"] != "paused":
            raise ValueError("Grant rebinding requires a paused code-edit checkpoint")
        if payload["approval_grant_id"] != expected_previous_grant_id:
            raise ValueError("Copilot checkpoint previous Grant ID does not match")
        if approval_grant_id == expected_previous_grant_id:
            raise ValueError("Copilot checkpoint replacement Grant ID must be new")
        self._validate_workspace(self._request(payload))
        payload["approval_grant_id"] = approval_grant_id
        payload["updated_at"] = datetime.now(UTC).isoformat()
        return self._write(checkpoint_root.resolve() / self._FILENAME, payload)

    def attach_rehearsal(
        self,
        *,
        checkpoint_root: Path,
        proposal_file: Path,
    ) -> dict[str, Any]:
        """Attach a non-executable Codex proposal without touching the target worktree."""

        payload = self.load(checkpoint_root)
        if payload["phase"] != "code_edit" or payload["status"] != "paused":
            raise ValueError("Codex rehearsal requires a paused code-edit checkpoint")
        request = self._request(payload)
        evidence = self._validate_workspace(request)
        if evidence.changed_paths:
            raise ValueError("Codex rehearsal requires an unchanged target worktree")

        root = checkpoint_root.resolve(strict=True)
        workspace = request.workspace_root.resolve(strict=True)
        registered = request.registered_repository_root.resolve(strict=True)
        source = proposal_file.resolve(strict=True)
        if any(
            path.is_relative_to(target)
            for path in (root, source)
            for target in (registered, workspace)
        ):
            raise ValueError("Codex rehearsal files must stay outside target repositories")

        raw = source.read_bytes()
        value: object = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Codex rehearsal proposal must be a JSON object")
        proposal = cast(dict[str, Any], value)
        self._validate_rehearsal(proposal, payload=payload, workspace=workspace)

        target = root / self._REHEARSAL_FILENAME
        if target.exists():
            raise FileExistsError(f"Codex rehearsal already exists: {target}")
        normalized = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
        self._write_text(target, normalized)
        instructions = root / self._REHEARSAL_INSTRUCTIONS_FILENAME
        self._write_text(instructions, self._rehearsal_instructions(payload))

        after = self._validate_workspace(request)
        if after.changed_paths:
            raise RuntimeError("Attaching Codex rehearsal changed the target worktree")
        return {
            "proposal_path": str(target),
            "instructions_path": str(instructions),
            "proposal_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "status": proposal["status"],
            "executable": False,
            "required_final_editor": "github_copilot_vscode",
        }

    def load_rehearsal(self, checkpoint_root: Path) -> dict[str, Any]:
        """Load and revalidate an attached rehearsal against the current checkpoint."""

        payload = self.load(checkpoint_root)
        request = self._request(payload)
        evidence = self._validate_workspace(request)
        if evidence.changed_paths:
            raise ValueError("Codex rehearsal is stale because the target worktree changed")
        path = checkpoint_root.resolve(strict=True) / self._REHEARSAL_FILENAME
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Codex rehearsal proposal must be a JSON object")
        proposal = cast(dict[str, Any], value)
        self._validate_rehearsal(
            proposal,
            payload=payload,
            workspace=request.workspace_root.resolve(strict=True),
        )
        normalized = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
        return {
            "proposal_path": str(path),
            "proposal_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "proposal": proposal,
        }

    def _validate_workspace(self, request: CopilotCheckpointRequest) -> GitDiffEvidence:
        registered = request.registered_repository_root.resolve(strict=True)
        workspace = request.workspace_root.resolve(strict=True)
        if self._git.common_repository_dir(registered) != self._git.common_repository_dir(
            workspace
        ):
            raise ValueError("Copilot checkpoint Workspace is outside the registered Repository")
        if request.phase == "code_edit" and registered == workspace:
            raise ValueError("Code-edit checkpoint requires an isolated linked worktree")
        return self._git.inspect_worktree(workspace, base_sha=request.base_revision)

    @staticmethod
    def _inside_target_repository(path: Path, request: CopilotCheckpointRequest) -> bool:
        resolved = path.resolve()
        return any(
            resolved.is_relative_to(target.resolve(strict=True))
            for target in (
                request.registered_repository_root,
                request.workspace_root,
            )
        )

    @staticmethod
    def _validate_rehearsal(
        proposal: dict[str, Any],
        *,
        payload: dict[str, Any],
        workspace: Path,
    ) -> None:
        required = {
            "schema_version",
            "proposal_id",
            "producer",
            "generated_at",
            "status",
            "bindings",
            "execution_policy",
            "changes",
            "findings",
            "validation_steps",
        }
        if set(proposal) != required or proposal["schema_version"] != "v1":
            raise ValueError("Codex rehearsal fields are invalid")
        if proposal["producer"] != "openai_codex":
            raise ValueError("Codex rehearsal producer must be openai_codex")
        if proposal["status"] not in {"ready_for_copilot_review", "needs_reanalysis"}:
            raise ValueError("Codex rehearsal status is invalid")
        for key in ("proposal_id", "generated_at"):
            if not isinstance(proposal[key], str) or not proposal[key].strip():
                raise ValueError(f"Codex rehearsal {key} must not be blank")
        try:
            generated_at = datetime.fromisoformat(
                str(proposal["generated_at"]).replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError("Codex rehearsal generated_at must be ISO-8601") from error
        if generated_at.tzinfo is None:
            raise ValueError("Codex rehearsal generated_at must include a timezone")

        bindings = proposal["bindings"]
        expected_bindings = {
            "session_id": payload["session_id"],
            "project_id": payload["project_id"],
            "analysis_case_id": payload["analysis_case_id"],
            "edit_packet_id": payload["edit_packet_id"],
            "base_revision": payload["base_revision"],
        }
        if bindings != expected_bindings:
            raise ValueError("Codex rehearsal bindings do not match the checkpoint")
        if proposal["execution_policy"] != {
            "executable": False,
            "automatic_apply_allowed": False,
            "required_final_editor": "github_copilot_vscode",
            "copilot_must_revalidate": True,
        }:
            raise ValueError("Codex rehearsal execution policy is invalid")

        changes = proposal["changes"]
        findings = proposal["findings"]
        validation_steps = proposal["validation_steps"]
        if not isinstance(changes, list) or not isinstance(findings, list):
            raise ValueError("Codex rehearsal changes and findings must be arrays")
        if not isinstance(validation_steps, list) or not validation_steps or any(
            not isinstance(step, str) or not step.strip() for step in validation_steps
        ):
            raise ValueError("Codex rehearsal validation_steps must be non-blank strings")
        if any(not isinstance(finding, str) or not finding.strip() for finding in findings):
            raise ValueError("Codex rehearsal findings must be non-blank strings")
        if proposal["status"] == "needs_reanalysis":
            if changes or not findings:
                raise ValueError("A needs_reanalysis rehearsal must contain findings only")
            return

        expected_paths = set(str(value) for value in payload["expected_outputs"])
        observed_paths: set[str] = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {
                "path",
                "operation",
                "candidate_patch",
                "candidate_content",
                "rationale",
            }:
                raise ValueError("Codex rehearsal change fields are invalid")
            path = change["path"]
            if not isinstance(path, str) or not path.strip():
                raise ValueError("Codex rehearsal change path must not be blank")
            pure_path = PurePosixPath(path)
            if pure_path.is_absolute() or ".." in pure_path.parts or str(pure_path) != path:
                raise ValueError("Codex rehearsal change path must be normalized and relative")
            if path in observed_paths:
                raise ValueError("Codex rehearsal change paths must be unique")
            observed_paths.add(path)
            operation = change["operation"]
            candidate_patch = change["candidate_patch"]
            candidate_content = change["candidate_content"]
            rationale = change["rationale"]
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("Codex rehearsal change rationale must not be blank")
            exists = (workspace / path).is_file()
            if operation == "modify":
                if (
                    not exists
                    or not isinstance(candidate_patch, str)
                    or not candidate_patch.strip()
                ):
                    raise ValueError("Codex rehearsal modify requires an existing file and patch")
                if candidate_content is not None:
                    raise ValueError("Codex rehearsal modify cannot contain candidate_content")
            elif operation == "add":
                if exists or candidate_patch is not None:
                    raise ValueError("Codex rehearsal add requires a new file without a patch")
                if not isinstance(candidate_content, str) or not candidate_content.strip():
                    raise ValueError("Codex rehearsal add requires candidate_content")
            else:
                raise ValueError("Codex rehearsal operation must be modify or add")
        if observed_paths != expected_paths:
            raise ValueError("Codex rehearsal changes must exactly cover checkpoint outputs")

    @staticmethod
    def _rehearsal_instructions(payload: dict[str, Any]) -> str:
        return (
            "# Copilot implementation rehearsal handoff\n\n"
            "This rehearsal is review-only and non-executable. Do not apply it as a patch.\n\n"
            "1. Re-read the active Edit Packet and current Approval Grant.\n"
            "2. Revalidate the linked worktree, Base Revision, current file contents, and scope.\n"
            "3. Use the Codex rehearsal only as a candidate implementation reference.\n"
            "4. Make every target-file edit yourself in VS Code GitHub Copilot.\n"
            "5. Stop for reanalysis if the proposal status is `needs_reanalysis`, "
            "the code differs, "
            "or any extra file/command is needed.\n"
            "6. Run only Grant-approved commands and record the final diff independently.\n\n"
            f"- Session: `{payload['session_id']}`\n"
            f"- Edit Packet: `{payload['edit_packet_id']}`\n"
            f"- Base Revision: `{payload['base_revision']}`\n"
        )

    @staticmethod
    def _request(payload: dict[str, Any]) -> CopilotCheckpointRequest:
        return CopilotCheckpointRequest(
            session_id=str(payload["session_id"]),
            phase=str(payload["phase"]),
            project_id=str(payload["project_id"]),
            analysis_case_id=str(payload["analysis_case_id"]),
            registered_repository_root=Path(str(payload["registered_repository_root"])),
            workspace_root=Path(str(payload["workspace_root"])),
            base_revision=str(payload["base_revision"]),
            expected_outputs=tuple(str(value) for value in payload["expected_outputs"]),
            approval_grant_id=(
                str(payload["approval_grant_id"])
                if payload.get("approval_grant_id") is not None
                else None
            ),
            edit_packet_id=(
                str(payload["edit_packet_id"])
                if payload.get("edit_packet_id") is not None
                else None
            ),
        )

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "session_id",
            "phase",
            "status",
            "project_id",
            "analysis_case_id",
            "registered_repository_root",
            "workspace_root",
            "base_revision",
            "approval_grant_id",
            "edit_packet_id",
            "expected_outputs",
            "pause_reason",
            "resume_count",
            "created_at",
            "updated_at",
        }
        if set(payload) != required or payload["schema_version"] != "v1":
            raise ValueError("Copilot checkpoint fields are invalid")
        if payload["status"] not in {"active", "paused"}:
            raise ValueError("Copilot checkpoint status is invalid")
        CopilotCheckpointService._request(payload)

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return payload

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
