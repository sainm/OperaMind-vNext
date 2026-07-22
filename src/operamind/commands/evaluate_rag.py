"""CLI for evaluating observed rankings against frozen Golden RAG expectations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from operamind.golden import RagQualityEvaluator


def build_parser() -> argparse.ArgumentParser:
    """Build the offline quality-gate parser."""

    parser = argparse.ArgumentParser(
        description="Evaluate RAG Recall@5/10, MRR, irrelevant rate, and Project leaks"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--observed", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print a machine-readable report and fail when any frozen threshold fails."""

    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve()
        expected = _load_object(_resolve(root, args.expected))
        observed = _load_object(_resolve(root, args.observed))
        dataset_root = root / "golden-dataset"
        _validate(
            expected,
            _load_object(dataset_root / "expected-rag-context.schema.json"),
            "expected",
        )
        _validate(
            observed,
            _load_object(dataset_root / "observed-rag-results.schema.json"),
            "observed",
        )
        result = RagQualityEvaluator().evaluate(expected=expected, observed=observed)
        print(
            json.dumps(
                {
                    "passed": result.passed,
                    "metrics": asdict(result.metrics),
                    "failures": list(result.failures),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.passed else 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _validate(instance: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda item: list(item.path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ValueError(f"{label} schema violation at {location}: {errors[0].message}")


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_object(path: Path) -> dict[str, Any]:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
