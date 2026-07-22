"""CLI for immutable Profile versions and audited project activation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import psycopg

from operamind.infrastructure.postgres import ProfileRepository
from operamind.profiles import ProfileCatalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage immutable OperaMind Profiles")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    store = commands.add_parser("store")
    store.add_argument("--profile-version-id", required=True)
    store.add_argument("--profile", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--profile-version-id", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--activation-event-id", required=True)
    activate.add_argument("--project-id", required=True)
    activate.add_argument("--binding-key", required=True)
    activate.add_argument("--profile-version-id", required=True)
    activate.add_argument("--activated-by", required=True)
    activate.add_argument("--reason", required=True)
    active = commands.add_parser("active")
    active.add_argument("--project-id", required=True)
    active.add_argument("--binding-key", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        root = args.root.resolve(strict=True)
        profiles = ProfileCatalog.load(root / "profiles")
        with psycopg.connect(database_url) as connection:
            repository = ProfileRepository(connection, profiles)
            if args.command == "store":
                profile_path = (
                    args.profile.resolve()
                    if args.profile.is_absolute()
                    else (root / args.profile).resolve()
                )
                payload = _load_object(profile_path)
                digest = repository.store_version(
                    profile_version_id=args.profile_version_id,
                    profile=payload,
                )
                output: object = {
                    "profile_version_id": args.profile_version_id,
                    "profile_type": payload["profile_type"],
                    "payload_digest": digest,
                }
            elif args.command == "inspect":
                inspected_profile = repository.get_version(args.profile_version_id)
                if inspected_profile is None:
                    raise ValueError("Profile version does not exist")
                output = {
                    "profile_version_id": args.profile_version_id,
                    "profile": inspected_profile,
                }
            elif args.command == "activate":
                created = repository.activate(
                    activation_event_id=args.activation_event_id,
                    project_id=args.project_id,
                    binding_key=args.binding_key,
                    profile_version_id=args.profile_version_id,
                    activated_by=args.activated_by,
                    reason=args.reason,
                )
                output = {
                    "activation_event_id": args.activation_event_id,
                    "created": created,
                    "project_id": args.project_id,
                    "binding_key": args.binding_key,
                    "profile_version_id": args.profile_version_id,
                }
            else:
                binding = repository.get_active(
                    project_id=args.project_id,
                    binding_key=args.binding_key,
                )
                if binding is None:
                    raise ValueError("Active Profile binding does not exist")
                output = {
                    "project_id": binding.project_id,
                    "binding_key": binding.binding_key,
                    "profile_version_id": binding.profile_version_id,
                    "activated_by": binding.activated_by,
                    "activated_at": binding.activated_at.isoformat(),
                    "profile": binding.profile,
                }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _load_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return cast(dict[str, Any], value)


if __name__ == "__main__":
    raise SystemExit(main())
