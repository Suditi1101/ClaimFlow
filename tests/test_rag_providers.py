import math
from unittest.mock import Mock, patch
import pytest
from verification.rag_clients import RagError, embed, chat_completion, config


@pytest.fixture(autouse=True)
def providers(settings):
    settings.CLAIM_RAG = {
        **settings.CLAIM_RAG,
        "groq_api_key": "synthetic-groq",
        "gemini_api_key": "synthetic-gemini",
        "chat_model": "openai/gpt-oss-120b",
        "embedding_model": "gemini-embedding-001",
    }


def response(payload):
    return Mock(status_code=200, content=b"{}", json=lambda: payload)


def test_gemini_batch_payload_order_and_normalization():
    first = [3.0, 4.0] + [0.0] * 1534
    second = [0.0, 2.0] + [0.0] * 1534
    with patch(
        "verification.rag_clients.requests.request",
        return_value=response({"embeddings": [{"values": first}, {"values": second}]}),
    ) as request:
        vectors = embed(["policy clause one", "policy clause two"])
    assert vectors[0][:2] == [0.6, 0.8] and vectors[1][1] == 1.0
    assert math.isclose(math.hypot(*vectors[0]), 1.0)
    args = request.call_args
    assert args.args == (
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents",
    )
    assert args.kwargs["headers"] == {"x-goog-api-key": "synthetic-gemini"}
    inputs = args.kwargs["json"]["requests"]
    assert [item["content"]["parts"][0]["text"] for item in inputs] == [
        "policy clause one",
        "policy clause two",
    ]
    assert all(
        item["taskType"] == "RETRIEVAL_DOCUMENT"
        and item["outputDimensionality"] == 1536
        for item in inputs
    )
    assert args.kwargs["allow_redirects"] is False


def test_query_embedding_uses_retrieval_query():
    with patch(
        "verification.rag_clients.request_json",
        return_value={"embeddings": [{"values": [1.0] * 1536}]},
    ) as request:
        embed(["is hospitalization covered?"], task_type="RETRIEVAL_QUERY")
    assert (
        request.call_args.kwargs["json"]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
    )


def test_groq_endpoint_and_key_are_separate():
    payload = {"model": "openai/gpt-oss-120b", "messages": []}
    with patch(
        "verification.rag_clients.requests.request",
        return_value=response({"choices": []}),
    ) as request:
        chat_completion(payload)
    assert (
        request.call_args.args[1] == "https://api.groq.com/openai/v1/chat/completions"
    )
    assert request.call_args.kwargs["headers"] == {
        "Authorization": "Bearer synthetic-groq"
    }
    assert request.call_args.kwargs["json"] == payload


def test_embedding_does_not_require_groq_key(settings):
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "groq_api_key": ""}
    with patch(
        "verification.rag_clients.request_json",
        return_value={"embeddings": [{"values": [1.0] * 1536}]},
    ):
        assert len(embed(["policy clause"])[0]) == 1536
    with pytest.raises(RagError, match="groq_not_configured"):
        chat_completion({})


def test_generation_does_not_require_gemini_key(settings):
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "gemini_api_key": ""}
    with patch("verification.rag_clients.request_json", return_value={"choices": []}):
        assert chat_completion({}) == {"choices": []}
    with pytest.raises(RagError, match="gemini_not_configured"):
        embed(["policy clause"])


@pytest.mark.parametrize("vector", [[0.0] * 1536, [float("nan")] * 1536, [1.0] * 768])
def test_invalid_gemini_vectors_fail_closed(vector):
    with patch(
        "verification.rag_clients.request_json",
        return_value={"embeddings": [{"values": vector}]},
    ):
        with pytest.raises(RagError, match="invalid_embeddings"):
            embed(["policy clause"])


def test_gemini_result_count_must_match():
    with patch(
        "verification.rag_clients.request_json", return_value={"embeddings": []}
    ):
        with pytest.raises(RagError, match="invalid_embeddings"):
            embed(["policy clause"])


def test_model_id_with_provider_prefix_is_valid():
    assert config(chat=True)["chat_model"] == "openai/gpt-oss-120b"
