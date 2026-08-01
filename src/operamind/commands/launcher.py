"""Single customer-facing OperaMind launcher and internal MCP child entry point."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from contextlib import suppress
from importlib.metadata import (
    PackageNotFoundError,
)
from importlib.metadata import (
    version as distribution_version,
)
from pathlib import Path

from operamind.commands import mcp_server, web
from operamind.local_installation import (
    PRODUCT_ID,
    ensure_bridge_token,
    installation_paths,
    load_environment_candidates,
    prepare_runtime_root,
    source_resource_root,
    write_runtime_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start OperaMind")
    parser.add_argument("--mcp", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--env-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--package-smoke-test", action="store_true", help=argparse.SUPPRESS)
    return parser


def _is_operamind_running(url: str, *, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return (
            response.status == 200
            and isinstance(payload, dict)
            and payload.get("product") == PRODUCT_ID
        )
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        return False


def _open_when_ready(url: str) -> None:
    for _attempt in range(80):
        if _is_operamind_running(url):
            webbrowser.open(url)
            return
        time.sleep(0.1)


def _report_error(message: str) -> None:
    """Keep launch failures visible even in a windowed packaged application."""

    if sys.stderr is not None:
        print(f"error: {message}", file=sys.stderr)
    if sys.platform == "win32":
        with suppress(AttributeError, OSError):
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "OperaMind", 0x10)
    elif sys.platform == "darwin":
        escaped = message.replace("\\", "\\\\").replace('"', '\\"')
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display dialog "{escaped}" with title "OperaMind" buttons {{"OK"}}',
                ],
                check=False,
                capture_output=True,
                timeout=10,
            )


def _verify_packaged_document_runtime() -> None:
    """Require extractor provenance metadata in the distributable runtime."""

    missing: list[str] = []
    for distribution in ("openpyxl", "python-docx"):
        try:
            distribution_version(distribution)
        except PackageNotFoundError:
            missing.append(distribution)
    if missing:
        raise ValueError(
            "Document extractor package metadata is missing: " + ", ".join(missing)
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = (args.root or source_resource_root()).expanduser().resolve()
    paths = installation_paths(data_directory=args.data_directory)
    try:
        runtime_root = prepare_runtime_root(source_root, paths)
        if args.package_smoke_test:
            _verify_packaged_document_runtime()
            return 0
        candidates = []
        if args.env_file is not None:
            candidates.append(
                args.env_file
                if args.env_file.is_absolute()
                else source_root / args.env_file
            )
        else:
            candidates.append(source_root / ".env")
        load_environment_candidates(paths, candidates)
        token = ensure_bridge_token(paths.bridge_token_file)
    except (OSError, UnicodeError, ValueError) as error:
        _report_error(f"OperaMind の準備に失敗しました。\n\n{error}")
        return 2
    os.environ["OPERAMIND_BRIDGE_TOKEN"] = token
    if not os.getenv("OPERAMIND_DATABASE_URL", "").strip():
        message = (
            "OPERAMIND_DATABASE_URL が設定されていません。\n\n"
            f"ユーザー設定を確認してください: {paths.config_file}"
        )
        if args.mcp:
            if sys.stderr is not None:
                print(f"error: {message}", file=sys.stderr)
        else:
            _report_error(message)
        return 2
    web_url = f"http://{args.host}:{args.port}"
    try:
        write_runtime_manifest(paths, resource_root=runtime_root, web_url=web_url)
    except (OSError, ValueError) as error:
        _report_error(f"OperaMind の実行情報を保存できません。\n\n{error}")
        return 2

    if args.mcp:
        return mcp_server.main(("--root", str(runtime_root)))
    if _is_operamind_running(web_url):
        if not args.no_browser:
            webbrowser.open(web_url)
        return 0
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(web_url,), daemon=True).start()
    result = web.main(
        ("--root", str(runtime_root), "--host", args.host, "--port", str(args.port))
    )
    if result != 0:
        _report_error(
            "OperaMind Web を起動できません。設定ファイルと PostgreSQL の状態を確認してください。"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
