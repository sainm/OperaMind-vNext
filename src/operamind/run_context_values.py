"""Dependency-neutral canonical values shared by Run orchestration and persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def canonical_digest(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_test_data_token(*, project_id: str, run_id: str, started_at: datetime) -> str:
    suffix = hashlib.sha256(f"{project_id}\0{run_id}".encode()).hexdigest()[:8].upper()
    return f"OM-E2E-{started_at:%Y%m%d}-{suffix}"
