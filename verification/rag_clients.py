"""Small REST adapters. Configuration is operator-owned; no user-provided URLs."""

import math
import logging
import time
import re
from urllib.parse import urlsplit
import requests
from django.conf import settings


log = logging.getLogger(__name__)


class RagError(Exception):
    """Safe error code and a fixed validation reason, never provider text."""

    def __init__(self, code, *, detail=""):
        super().__init__(code)
        self.detail = detail


def config(*, chat=False, embeddings=False):
    cfg = settings.CLAIM_RAG
    if chat and not cfg["groq_api_key"]:
        raise RagError("groq_not_configured")
    if embeddings and not cfg["gemini_api_key"]:
        raise RagError("gemini_not_configured")
    if not re.fullmatch(
        r"[A-Za-z0-9_.:/-]{1,100}", cfg["chat_model"]
    ) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", cfg["embedding_model"]):
        raise RagError("invalid_model_configuration")
    try:
        url = urlsplit(cfg["qdrant_url"])
    except ValueError as exc:
        raise RagError("invalid_qdrant_configuration") from exc
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or (
            url.scheme == "http"
            and url.hostname not in {"qdrant", "localhost", "127.0.0.1"}
        )
    ):
        raise RagError("invalid_qdrant_configuration")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", cfg["collection"]):
        raise RagError("invalid_collection")
    return cfg


def safe_label(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value)
        else "unknown"
    )


def request_json(method, url, **kwargs):
    host = urlsplit(url).hostname
    provider = (
        "groq"
        if host == "api.groq.com"
        else ("gemini" if host == "generativelanguage.googleapis.com" else "qdrant")
    )
    started = time.monotonic()
    try:
        response = requests.request(
            method, url, timeout=(5, 30), allow_redirects=False, **kwargs
        )
        log.info(
            "rag_api_response provider=%s status=%s duration_ms=%s request_id=%s",
            provider,
            response.status_code,
            round((time.monotonic() - started) * 1000),
            safe_label(response.headers.get("x-request-id", "unknown")),
        )
        if response.status_code == 429:
            raise RagError("provider_rate_limited")
        if response.status_code in {401, 403}:
            raise RagError("provider_auth_failed")
        if not 200 <= response.status_code < 300:
            raise RagError("provider_unavailable")
        if len(response.content) > 4 * 1024 * 1024:
            raise RagError("provider_response_too_large")
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning(
            "rag_api_failed provider=%s error_type=%s", provider, type(exc).__name__
        )
        raise RagError("provider_unavailable") from exc


def chat_completion(payload):
    cfg = config(chat=True)
    response = request_json(
        "POST",
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + cfg["groq_api_key"]},
        json=payload,
    )

    if isinstance(response, dict):
        choices = response.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else {}
        )
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        tokens = {
            key: value
            for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and type(value) is int
        }
        log.info(
            "rag_model_response request_id=%s model=%s finish_reason=%s usage=%s",
            safe_label(response.get("id")),
            safe_label(response.get("model")),
            safe_label(choice.get("finish_reason")),
            tokens,
        )
    return response


def embed(texts, *, task_type="RETRIEVAL_DOCUMENT"):
    cfg = config(embeddings=True)
    if task_type not in {"RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"}:
        raise RagError("invalid_embedding_task")
    if (
        not texts
        or len(texts) > 16
        or any(not isinstance(text, str) or not text.strip() for text in texts)
    ):
        raise RagError("invalid_embedding_input")
    model = "models/" + cfg["embedding_model"]
    response = request_json(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/"
        + model
        + ":batchEmbedContents",
        headers={"x-goog-api-key": cfg["gemini_api_key"]},
        json={
            "requests": [
                {
                    "model": model,
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": 1536,
                }
                for text in texts
            ]
        },
    )
    try:
        vectors = [item["values"] for item in response["embeddings"]]
        if len(vectors) != len(texts):
            raise ValueError
        normalized = []
        for vector in vectors:
            if len(vector) != 1536 or any(
                type(n) not in (int, float) or not math.isfinite(n) for n in vector
            ):
                raise ValueError
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError
            normalized.append([value / norm for value in vector])
        return normalized
    except (KeyError, TypeError, ValueError) as exc:
        raise RagError("invalid_embeddings") from exc


def qdrant(method, path, payload=None):
    cfg = config()
    headers = {"api-key": cfg["qdrant_key"]} if cfg["qdrant_key"] else {}
    return request_json(
        method,
        cfg["qdrant_url"].rstrip("/") + "/collections/" + cfg["collection"] + path,
        headers=headers,
        **({"json": payload} if payload is not None else {}),
    )


def ensure_collection():
    cfg = config()
    # Qdrant creation is explicit and idempotent; do not replace existing collections.
    result = request_json(
        "GET",
        cfg["qdrant_url"].rstrip("/") + "/collections",
        headers={"api-key": cfg["qdrant_key"]} if cfg["qdrant_key"] else {},
    )
    try:
        collections = [item["name"] for item in result["result"]["collections"]]
    except (KeyError, TypeError) as exc:
        raise RagError("invalid_vector_response") from exc
    if cfg["collection"] not in collections:
        qdrant("PUT", "", {"vectors": {"size": 1536, "distance": "Cosine"}})
