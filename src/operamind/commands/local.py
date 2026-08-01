"""Load one repository environment and launch a primary local entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from operamind.commands import mcp_server, migrate, web
from operamind.environment_file import load_environment_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OperaMind with one repository-local environment file"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="environment file, relative to --root unless absolute",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")

    web_parser = commands.add_parser("web", help="start the local Web application")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    mcp_parser = commands.add_parser("mcp", help="start the local MCP stdio server")
    mcp_parser.add_argument("--max-tool-calls", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    env_path = args.env_file if args.env_file.is_absolute() else root / args.env_file
    try:
        load_environment_file(env_path)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"error: failed to load environment file: {error}", file=sys.stderr)
        return 2

    if args.command == "migrate":
        return migrate.main(("--root", str(root)))
    if args.command == "web":
        return web.main(("--root", str(root), "--host", args.host, "--port", str(args.port)))
    if args.command == "mcp":
        return mcp_server.main(("--root", str(root), "--max-tool-calls", str(args.max_tool_calls)))
    raise AssertionError(f"Unsupported local command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
