"""Read-model compatibility for immutable versioned Artifacts."""

from __future__ import annotations

from typing import Any, cast

_LEGACY_CLOSURE_REASON = (
    "Legacy ChangeClosureResult v1 requires changed-line coverage re-evaluation"
)


def project_change_closure_result(artifact: dict[str, Any]) -> dict[str, Any]:
    """Project a stored Closure into the current fail-closed read model.

    The returned compatibility fields are not part of the immutable v1 Artifact and
    must never be written back to the Artifact repository.
    """

    if artifact.get("artifact_type") != "ChangeClosureResult":
        raise ValueError("Compatibility projection requires a ChangeClosureResult")
    version = artifact.get("schema_version")
    if version == "v2":
        return artifact
    if version != "v1":
        raise ValueError(f"Unsupported ChangeClosureResult schema_version: {version!r}")

    unresolved = {str(value) for value in cast(list[object], artifact.get("unresolved_items", []))}
    unresolved.add(_LEGACY_CLOSURE_REASON)
    projected = dict(artifact)
    projected.update(
        {
            "compatibility_status": "stale",
            "changed_line_coverage_percent": 0.0,
            "changed_line_coverage_status": "missing",
            "status": (
                "reanalysis_required"
                if artifact.get("status") == "reanalysis_required"
                else "blocked"
            ),
            "unresolved_items": sorted(unresolved),
        }
    )
    return projected


__all__ = ["project_change_closure_result"]
