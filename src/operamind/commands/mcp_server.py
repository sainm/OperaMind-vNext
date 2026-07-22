"""Start the local newline-delimited MCP stdio server."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from operamind.mcp import CopilotToolDispatcher, OperaMindMcpServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the OperaMind MCP stdio server")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-tool-calls", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("OPERAMIND_DATABASE_URL")
    if not database_url:
        print("error: OPERAMIND_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            OperaMindMcpServer(
                CopilotToolDispatcher(connection=connection, root=args.root.resolve()),
                max_tool_calls=args.max_tool_calls,
            ).serve(sys.stdin, sys.stdout)
        return 0
    except (OSError, RuntimeError, ValueError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
