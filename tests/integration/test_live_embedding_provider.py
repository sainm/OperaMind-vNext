import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from operamind.infrastructure.embeddings import OpenAICompatibleEmbeddingProvider
from operamind.profiles import ProfileCatalog

ROOT = Path(__file__).parents[2]
LIVE_ENABLED = os.getenv("OPERAMIND_EMBEDDING_LIVE") == "1"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not LIVE_ENABLED,
    reason="OPERAMIND_EMBEDDING_LIVE is not set to 1",
)
def test_openai_compatible_embedding_provider_live_contract() -> None:
    """Probe a real configured Provider without persisting credentials or vectors."""

    profile_path_value = os.getenv("OPERAMIND_EMBEDDING_LIVE_PROFILE")
    if profile_path_value is None or not profile_path_value.strip():
        pytest.fail("OPERAMIND_EMBEDDING_LIVE_PROFILE must name a validated Profile JSON")
    profile_path = Path(profile_path_value).expanduser().resolve()
    profile_value: object = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(profile_value, dict):
        pytest.fail("Live Embedding Profile must be a JSON object")
    profile = cast(dict[str, Any], profile_value)
    ProfileCatalog.load(ROOT / "profiles").validate_profile(profile)

    provider = OpenAICompatibleEmbeddingProvider.from_profile(profile)
    probe = provider.probe()
    configured_model = os.environ[str(profile["model_env"])]
    expected_dimensions = int(profile["expected_dimensions"])

    assert probe.model == configured_model
    assert probe.dimensions == expected_dimensions

    batch = provider.embed(
        (
            "OperaMind verifies an expense status filter.",
            "OperaMind verifies an invoice approval workflow.",
        )
    )
    assert batch.model == probe.model
    assert len(batch.vectors) == 2
    assert all(len(vector) == expected_dimensions for vector in batch.vectors)
    assert all(any(value != 0.0 for value in vector) for vector in batch.vectors)
    assert batch.vectors[0] != batch.vectors[1]
