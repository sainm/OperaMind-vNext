"""CLI for Copilot-authored, stepwise-confirmed change-loop drafts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from operamind.application.change_draft import (
    ChangeDraftInputMode,
    ChangeDraftRequest,
    ChangeDraftService,
)
from operamind.application.change_draft_session import ChangeDraftSessionService
from operamind.application.change_loop_batch import IsolatedGitWorktree
from operamind.application.copilot_checkpoint import (
    CopilotCheckpointRequest,
    CopilotCheckpointService,
)
from operamind.infrastructure.draft_generation import FileDraftGenerationProvider


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a VS Code GitHub Copilot handoff, import its response, "
            "confirm and materialize a non-executable candidate"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare", help="Prepare a bounded handoff for VS Code GitHub Copilot"
    )
    prepare_entries = prepare.add_subparsers(dest="entry", required=True)
    prepare_documents = prepare_entries.add_parser(
        "documents", help="Use before and after documents"
    )
    prepare_requirement = prepare_entries.add_parser(
        "requirement", help="Use a natural-language requirement"
    )
    for entry in (prepare_documents, prepare_requirement):
        entry.add_argument("--handoff-root", type=Path, required=True)
        _add_draft_input_arguments(entry)
    prepare_documents.add_argument("--after-document", type=Path, required=True)
    prepare_requirement.add_argument("--requirement", required=True)

    generate = commands.add_parser(
        "generate", help="Import a VS Code GitHub Copilot response as an unapproved draft"
    )
    entries = generate.add_subparsers(dest="entry", required=True)
    documents = entries.add_parser("documents", help="Use before and after documents")
    requirement = entries.add_parser("requirement", help="Use a natural-language requirement")
    for entry in (documents, requirement):
        _add_generation_arguments(entry)
    documents.add_argument("--after-document", type=Path, required=True)
    requirement.add_argument("--requirement", required=True)

    show = commands.add_parser("next", help="Show the next unanswered confirmation")
    show.add_argument("--draft-root", type=Path, required=True)

    answer = commands.add_parser("answer", help="Select one option for a confirmation")
    answer.add_argument("--draft-root", type=Path, required=True)
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--option-id", required=True)
    answer.add_argument("--answered-by", required=True)

    approve = commands.add_parser(
        "approve", help="Review and materialize a non-executable candidate case"
    )
    approve.add_argument("--draft-root", type=Path, required=True)
    approve.add_argument("--case-root", type=Path, required=True)
    approve.add_argument("--target-repository", type=Path, required=True)
    approve.add_argument("--reviewed-by", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        sessions = ChangeDraftSessionService(repository_root=root)
        if args.command == "prepare":
            result = _prepare(root, args)
            print(
                json.dumps(
                    {
                        "status": "prepared",
                        "handoff_root": str(result.handoff_root),
                        "prompt": str(result.prompt_path),
                        "response_schema": str(result.response_schema_path),
                        "response_target": str(result.response_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "generate":
            result = _generate(root, args)
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "draft_root": str(result.session_path.parent),
                        "session": str(result.session_path),
                        "next_question": sessions.next_question(result.session_path.parent),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "next":
            draft_root = _resolve(root, args.draft_root)
            session = sessions.load(draft_root)
            print(
                json.dumps(
                    {
                        "status": session["status"],
                        "next_question": sessions.next_question(draft_root),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "answer":
            answered = sessions.answer(
                draft_root=_resolve(root, args.draft_root),
                question_id=args.question_id,
                option_id=args.option_id,
                answered_by=args.answered_by,
            )
            print(
                json.dumps(
                    {
                        "status": answered.session["status"],
                        "session": str(answered.session_path),
                        "next_question": answered.next_question,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        approved = sessions.approve(
            draft_root=_resolve(root, args.draft_root),
            final_case_root=_resolve(root, args.case_root),
            target_repository=_resolve(root, args.target_repository),
            reviewed_by=args.reviewed_by,
        )
        payload: dict[str, object] = {
            "status": "candidate_materialized",
            "case_id": approved.case.case_id,
            "case_root": str(approved.case_root),
            "executable": False,
            "next_step": "promote through the Canonical RAG/Impact/Grant pipeline",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--draft-root", type=Path, required=True)
    parser.add_argument("--response-file", type=Path, required=True)
    _add_draft_input_arguments(parser)


def _add_draft_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target-repository", type=Path, required=True)
    parser.add_argument("--base-revision", default="HEAD")
    parser.add_argument("--before-document", type=Path, required=True)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--application-root", required=True)
    parser.add_argument("--scan-root", action="append", required=True)
    parser.add_argument(
        "--code-profile",
        default="profiles/code-framework-profile.example.json",
    )
    parser.add_argument(
        "--document-profile",
        default="profiles/screen-design-convention-profile.example.json",
    )
    parser.add_argument("--max-candidate-files", type=int, default=12)


def _prepare(root: Path, args: argparse.Namespace) -> Any:
    handoff_root = _resolve(root, args.handoff_root)
    repository = _resolve(root, args.target_repository)
    revision = _git_revision(repository, args.base_revision)
    worktree_path = handoff_root.parent / f".{handoff_root.name}-preparation-worktree"
    with IsolatedGitWorktree(
        repository=repository,
        path=worktree_path,
        revision=revision,
    ) as workspace:
        result = ChangeDraftService(repository_root=root).prepare_handoff(
            _draft_request(args, root=root, workspace=workspace, output_root=handoff_root),
            handoff_root=handoff_root,
        )
    CopilotCheckpointService().initialize(
        checkpoint_root=handoff_root,
        request=CopilotCheckpointRequest(
            session_id=args.draft_id,
            phase="draft_generation",
            project_id=args.project_id,
            analysis_case_id=args.case_id,
            registered_repository_root=repository,
            workspace_root=repository,
            base_revision=revision,
            expected_outputs=("ai-response.json",),
        ),
    )
    return result


def _generate(root: Path, args: argparse.Namespace) -> Any:
    draft_root = _resolve(root, args.draft_root)
    repository = _resolve(root, args.target_repository)
    revision = _git_revision(repository, args.base_revision)
    worktree_path = draft_root.parent / f".{draft_root.name}-generation-worktree"
    provider = FileDraftGenerationProvider(
        repository_root=root,
        response_path=_resolve(root, args.response_file),
    )
    with IsolatedGitWorktree(
        repository=repository,
        path=worktree_path,
        revision=revision,
    ) as workspace:
        return ChangeDraftService(repository_root=root, provider=provider).generate(
            _draft_request(args, root=root, workspace=workspace, output_root=draft_root)
        )


def _draft_request(
    args: argparse.Namespace,
    *,
    root: Path,
    workspace: Path,
    output_root: Path,
) -> ChangeDraftRequest:
    mode = (
        ChangeDraftInputMode.DOCUMENTS
        if args.entry == "documents"
        else ChangeDraftInputMode.NATURAL_LANGUAGE
    )
    return ChangeDraftRequest(
        draft_id=args.draft_id,
        case_id=args.case_id,
        project_id=args.project_id,
        repository_id=args.repository_id,
        workspace_root=workspace,
        before_document=_resolve(root, args.before_document),
        after_document=(_resolve(root, args.after_document) if args.entry == "documents" else None),
        requirement_text=args.requirement if args.entry == "requirement" else None,
        input_mode=mode,
        application_root=args.application_root,
        scan_roots=tuple(args.scan_root),
        code_profile=args.code_profile,
        document_profile=args.document_profile,
        output_root=output_root,
        max_candidate_files=args.max_candidate_files,
    )


def _git_revision(repository: Path, revision: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repository), "rev-parse", "--verify", f"{revision}^{{commit}}"),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"Git revision was not found: {revision}")
    return result.stdout.strip()


def _resolve(root: Path, value: Path) -> Path:
    return value.expanduser().resolve() if value.is_absolute() else (root / value).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
