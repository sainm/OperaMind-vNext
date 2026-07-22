"""Embedding Provider port and strict OpenAI-compatible HTTP adapter."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class EmbeddingProviderError(RuntimeError):
    """Raised when a Provider is unavailable or violates its response contract."""


class EmbeddingTransportError(EmbeddingProviderError):
    """Transport failure with an explicit retry classification."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class EmbeddingProviderProbe:
    """Observed runtime model identity and output dimensions."""

    model: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """One ordered Provider response."""

    model: str
    vectors: tuple[tuple[float, ...], ...]


class EmbeddingProvider(Protocol):
    """Port used by the index build service."""

    def probe(self) -> EmbeddingProviderProbe: ...

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch: ...


class EmbeddingHttpTransport(Protocol):
    """Small JSON HTTP boundary that can be replaced by a deterministic Fake."""

    def post_json(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


class UrllibEmbeddingHttpTransport:
    """Bounded stdlib HTTP transport without response-body logging."""

    max_response_bytes = 10 * 1024 * 1024

    def post_json(
        self,
        *,
        endpoint: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(
            endpoint,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            raise EmbeddingTransportError(
                f"Embedding HTTP status {error.code}",
                retryable=error.code == 429 or error.code >= 500,
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise EmbeddingTransportError(
                "Embedding HTTP transport failed",
                retryable=True,
            ) from error
        if len(body) > self.max_response_bytes:
            raise EmbeddingTransportError(
                "Embedding HTTP response exceeds the configured limit",
                retryable=False,
            )
        try:
            decoded: object = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EmbeddingTransportError(
                "Embedding HTTP response is not valid JSON",
                retryable=False,
            ) from error
        if not isinstance(decoded, dict):
            raise EmbeddingTransportError(
                "Embedding HTTP response must be a JSON object",
                retryable=False,
            )
        return cast(dict[str, Any], decoded)


class OpenAICompatibleEmbeddingProvider:
    """Strict `/embeddings` adapter configured only through environment indirection."""

    _probe_text = "operamind embedding dimension probe"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        max_retries: int,
        transport: EmbeddingHttpTransport,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("Embedding API key and model must not be blank")
        if timeout_seconds <= 0 or max_retries < 0:
            raise ValueError("Embedding timeout/retry settings are invalid")
        self._endpoint = _embedding_endpoint(endpoint)
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._transport = transport
        self._sleeper = sleeper

    @classmethod
    def from_profile(
        cls,
        profile: dict[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
        transport: EmbeddingHttpTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> OpenAICompatibleEmbeddingProvider:
        """Resolve only the environment variable names declared by a validated Profile."""

        if profile.get("profile_type") != "EmbeddingProfile":
            raise ValueError("OpenAI-compatible Provider requires an EmbeddingProfile")
        if profile.get("provider") != "openai_compatible":
            raise ValueError("Unsupported embedding provider")
        environment = os.environ if environ is None else environ
        values: dict[str, str] = {}
        for field in ("base_url_env", "api_key_env", "model_env"):
            variable = str(profile[field])
            value = environment.get(variable)
            if value is None or not value.strip():
                raise ValueError(f"Required embedding environment variable is missing: {variable}")
            values[field] = value
        return cls(
            endpoint=values["base_url_env"],
            api_key=values["api_key_env"],
            model=values["model_env"],
            timeout_seconds=int(profile["timeout_seconds"]),
            max_retries=int(profile["max_retries"]),
            transport=transport or UrllibEmbeddingHttpTransport(),
            sleeper=sleeper,
        )

    def probe(self) -> EmbeddingProviderProbe:
        """Embed one fixed probe and return actual model/dimensions."""

        batch = self.embed((self._probe_text,))
        return EmbeddingProviderProbe(model=batch.model, dimensions=len(batch.vectors[0]))

    def embed(self, texts: tuple[str, ...]) -> EmbeddingBatch:
        """Return vectors in input order and reject any Provider contract drift."""

        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty text")
        response = self._post_with_retry(texts)
        response_model = response.get("model")
        if response_model != self._model:
            raise EmbeddingProviderError(
                "Embedding response model does not match the configured model"
            )
        data = response.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingProviderError("Embedding response item count does not match input")

        by_index: dict[int, tuple[float, ...]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("Embedding response item must be an object")
            index = item.get("index")
            raw_vector = item.get("embedding")
            if not isinstance(index, int) or isinstance(index, bool):
                raise EmbeddingProviderError("Embedding response index must be an integer")
            if index in by_index or not 0 <= index < len(texts):
                raise EmbeddingProviderError(
                    "Embedding response index is duplicate or out of range"
                )
            if not isinstance(raw_vector, list) or not raw_vector:
                raise EmbeddingProviderError("Embedding response vector must be non-empty")
            vector: list[float] = []
            for value in raw_vector:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise EmbeddingProviderError("Embedding vector values must be numeric")
                converted = float(value)
                if not math.isfinite(converted):
                    raise EmbeddingProviderError("Embedding vector values must be finite")
                vector.append(converted)
            by_index[index] = tuple(vector)
        if set(by_index) != set(range(len(texts))):
            raise EmbeddingProviderError("Embedding response indices are incomplete")
        vectors = tuple(by_index[index] for index in range(len(texts)))
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1:
            raise EmbeddingProviderError("Embedding response dimensions are inconsistent")
        if any(not any(value != 0.0 for value in vector) for vector in vectors):
            raise EmbeddingProviderError("Embedding response contains an all-zero vector")
        return EmbeddingBatch(model=self._model, vectors=vectors)

    def _post_with_retry(self, texts: tuple[str, ...]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                return self._transport.post_json(
                    endpoint=self._endpoint,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "OperaMind-vNext/embedding",
                    },
                    payload={"input": list(texts), "model": self._model},
                    timeout_seconds=self._timeout_seconds,
                )
            except EmbeddingTransportError as error:
                if not error.retryable or attempt == self._max_retries:
                    raise
                self._sleeper(min(2**attempt, 8))
        raise AssertionError("Embedding retry loop did not return or raise")


def _embedding_endpoint(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("Embedding base URL must use http or https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Embedding base URL must not contain credentials, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Plain HTTP embedding endpoints are restricted to localhost")
    return f"{base_url.rstrip('/')}/embeddings"
