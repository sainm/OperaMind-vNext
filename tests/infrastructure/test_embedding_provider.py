from collections.abc import Mapping
from typing import Any

import pytest

from operamind.infrastructure.embeddings import (
    EmbeddingProviderError,
    EmbeddingTransportError,
    OpenAICompatibleEmbeddingProvider,
)


class FakeTransport:
    def __init__(self, outcomes: list[dict[str, Any] | EmbeddingTransportError]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, Mapping[str, str], dict[str, Any], int]] = []

    def post_json(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.calls.append((endpoint, headers, payload, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, EmbeddingTransportError):
            raise outcome
        return outcome


def profile() -> dict[str, Any]:
    return {
        "profile_type": "EmbeddingProfile",
        "profile_id": "test-embedding",
        "profile_version": "1.0.0",
        "provider": "openai_compatible",
        "base_url_env": "TEST_EMBED_URL",
        "api_key_env": "TEST_EMBED_KEY",
        "model_env": "TEST_EMBED_MODEL",
        "expected_dimensions": 3,
        "batch_size": 8,
        "timeout_seconds": 9,
        "max_retries": 2,
        "preprocessing_version": "canonical-slice-v1",
        "ranking_policy_version": "hybrid-rrf-v1",
    }


def test_provider_resolves_environment_and_restores_input_order() -> None:
    transport = FakeTransport(
        [
            {
                "model": "embedding-model-1",
                "data": [
                    {"index": 1, "embedding": [0, 1, 0]},
                    {"index": 0, "embedding": [1, 0, 0]},
                ],
            }
        ]
    )
    provider = OpenAICompatibleEmbeddingProvider.from_profile(
        profile(),
        environ={
            "TEST_EMBED_URL": "https://embedding.example.invalid/v1",
            "TEST_EMBED_KEY": "secret-not-persisted",
            "TEST_EMBED_MODEL": "embedding-model-1",
        },
        transport=transport,
        sleeper=lambda _seconds: None,
    )

    result = provider.embed(("first", "second"))

    assert result.model == "embedding-model-1"
    assert result.vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    endpoint, headers, payload, timeout = transport.calls[0]
    assert endpoint == "https://embedding.example.invalid/v1/embeddings"
    assert headers["Authorization"] == "Bearer secret-not-persisted"
    assert payload == {"input": ["first", "second"], "model": "embedding-model-1"}
    assert timeout == 9


def test_provider_probe_observes_actual_dimensions() -> None:
    transport = FakeTransport(
        [{"model": "embedding-model-1", "data": [{"index": 0, "embedding": [1, 2, 3]}]}]
    )
    provider = OpenAICompatibleEmbeddingProvider.from_profile(
        profile(),
        environ={
            "TEST_EMBED_URL": "http://127.0.0.1:9000/v1",
            "TEST_EMBED_KEY": "local-key",
            "TEST_EMBED_MODEL": "embedding-model-1",
        },
        transport=transport,
        sleeper=lambda _seconds: None,
    )

    assert provider.probe().dimensions == 3


def test_provider_retries_only_retryable_transport_errors() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            EmbeddingTransportError("temporary", retryable=True),
            {"model": "embedding-model-1", "data": [{"index": 0, "embedding": [1, 2, 3]}]},
        ]
    )
    provider = OpenAICompatibleEmbeddingProvider.from_profile(
        profile(),
        environ={
            "TEST_EMBED_URL": "https://embedding.example.invalid/v1",
            "TEST_EMBED_KEY": "key",
            "TEST_EMBED_MODEL": "embedding-model-1",
        },
        transport=transport,
        sleeper=sleeps.append,
    )

    assert provider.embed(("text",)).vectors == ((1.0, 2.0, 3.0),)
    assert sleeps == [1]


def test_provider_rejects_model_or_vector_contract_drift() -> None:
    transport = FakeTransport(
        [{"model": "other-model", "data": [{"index": 0, "embedding": [1, 2, 3]}]}]
    )
    provider = OpenAICompatibleEmbeddingProvider.from_profile(
        profile(),
        environ={
            "TEST_EMBED_URL": "https://embedding.example.invalid/v1",
            "TEST_EMBED_KEY": "key",
            "TEST_EMBED_MODEL": "embedding-model-1",
        },
        transport=transport,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(EmbeddingProviderError, match="model does not match"):
        provider.embed(("text",))


def test_provider_rejects_all_zero_vector() -> None:
    transport = FakeTransport(
        [{"model": "embedding-model-1", "data": [{"index": 0, "embedding": [0, 0, 0]}]}]
    )
    provider = OpenAICompatibleEmbeddingProvider.from_profile(
        profile(),
        environ={
            "TEST_EMBED_URL": "https://embedding.example.invalid/v1",
            "TEST_EMBED_KEY": "key",
            "TEST_EMBED_MODEL": "embedding-model-1",
        },
        transport=transport,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(EmbeddingProviderError, match="all-zero"):
        provider.embed(("text",))


def test_provider_rejects_plain_http_remote_endpoint() -> None:
    with pytest.raises(ValueError, match="restricted to localhost"):
        OpenAICompatibleEmbeddingProvider.from_profile(
            profile(),
            environ={
                "TEST_EMBED_URL": "http://embedding.example.invalid/v1",
                "TEST_EMBED_KEY": "key",
                "TEST_EMBED_MODEL": "embedding-model-1",
            },
            transport=FakeTransport([]),
        )
