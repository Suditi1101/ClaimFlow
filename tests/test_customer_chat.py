import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client

from tests.test_rag import claim as claim_fixture, rag_settings  # noqa: F401
from claims.models import ReviewDecision
from verification.chat import answer_question
from verification.models import ClaimAssessment
from verification.rag_clients import RagError

claim = claim_fixture
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


def reply(answer="Your claim is being reviewed. [C1]", ids=None):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "answer": answer,
                            "source_ids": ids if ids is not None else ["C1"],
                        }
                    )
                },
            }
        ]
    }


def test_chat_requires_login():
    response = Client().get("/accounts/chat/")
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_customer_cannot_select_another_customers_claim_or_policy(claim):
    other = get_user_model().objects.create_user("chat-other")
    client = Client()
    client.force_login(other)
    with patch("common.chat.answer_question") as provider:
        for context in [f"claim:{claim.pk}", f"policy:{claim.policy_id}"]:
            assert (
                client.get("/accounts/chat/", {"context": context}).status_code == 404
            )
            assert (
                client.post(
                    "/accounts/chat/", {"context": context, "question": "Show details"}
                ).status_code
                == 404
            )
    provider.assert_not_called()


def test_service_rechecks_ownership(claim):
    other = get_user_model().objects.create_user("unauthorized")
    with patch("verification.chat.retrieve") as retrieve:
        with pytest.raises(RagError, match="chat_context_forbidden"):
            answer_question(customer=other, question="Details", history=[], claim=claim)
    retrieve.assert_not_called()


def test_chat_csrf_and_rendering(claim):
    client = Client(enforce_csrf_checks=True)
    client.force_login(claim.customer)
    assert (
        client.post(
            "/accounts/chat/", {"context": f"claim:{claim.pk}", "question": "Status?"}
        ).status_code
        == 403
    )
    response = client.get("/accounts/chat/", {"context": f"claim:{claim.pk}"})
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store, private"
    assert b"Your question" in response.content
    assert b"Ask about this claim" in client.get("/accounts/").content


def test_chat_roundtrip_history_clear_and_no_decision_change(claim):
    client = Client()
    client.force_login(claim.customer)
    context = f"claim:{claim.pk}"
    turn = {
        "question": "Status?",
        "answer": "<script>alert(1)</script>",
        "sources": [],
        "notice": "",
    }
    with patch("common.chat.answer_question", return_value=turn) as answer:
        for _ in range(7):
            response = client.post(
                "/accounts/chat/", {"context": context, "question": "Status?"}
            )
            assert response.status_code == 302
        assert len(answer.call_args.kwargs["history"]) == 6
    response = client.get("/accounts/chat/", {"context": context})
    assert b"&lt;script&gt;" in response.content
    assert b"<script>alert(1)</script>" not in response.content
    session_key = "customer_chat_" + str(claim.customer_id)
    assert len(client.session[session_key][context]) == 6
    # A different context does not receive the previous claim conversation.
    with patch("common.chat.answer_question", return_value=turn) as answer:
        client.post(
            "/accounts/chat/",
            {"context": f"policy:{claim.policy_id}", "question": "Coverage?"},
        )
    assert answer.call_args.kwargs["history"] == []
    client.post("/accounts/chat/", {"context": context, "action": "clear"})
    assert context not in client.session[session_key]
    claim.refresh_from_db()
    assert claim.status == "submitted"
    assert not claim.reviewdecision_set.exists()


def test_recorded_rejection_is_authoritative_and_internal_findings_are_excluded(claim):
    claim.status = "rejected"
    claim.save(update_fields=["status"])
    ReviewDecision.objects.create(
        claim=claim,
        reviewer=claim.customer,
        decision="rejected",
        reason="Required itemized invoice was missing.",
    )
    ClaimAssessment.objects.create(
        claim=claim,
        status="completed",
        summary="STAFF SECRET",
        findings=[{"explanation": "STAFF SECRET"}],
        evidence=[
            {
                "document_id": "someone-elses-document",
                "text": "FOREIGN DOCUMENT",
                "page": 1,
            }
        ],
    )
    with (
        patch("verification.chat.retrieve", return_value=[]),
        patch("verification.chat.chat_completion", return_value=reply()) as provider,
    ):
        result = answer_question(
            customer=claim.customer, question="Why rejected?", history=[], claim=claim
        )
    data = json.loads(provider.call_args.args[0]["messages"][1]["content"])
    assert "Required itemized invoice was missing." in json.dumps(data)
    assert "STAFF SECRET" not in json.dumps(data)
    assert "FOREIGN DOCUMENT" not in json.dumps(data)
    assert result["sources"][0]["id"] == "C1"


def test_policy_only_chat_uses_owned_policy_and_valid_date(claim):
    with (
        patch(
            "verification.chat.retrieve",
            return_value=[
                {
                    "title": "Policy wording",
                    "version": "v1",
                    "page": 2,
                    "text": "An invoice is required.",
                }
            ],
        ) as retrieve,
        patch(
            "verification.chat.chat_completion",
            return_value=reply("An invoice is required. [P1]", ["P1"]),
        ),
    ):
        result = answer_question(
            customer=claim.customer,
            question="Which documents?",
            history=[],
            policy=claim.policy,
        )
    scope = retrieve.call_args.args[0]
    assert scope.policy_id == claim.policy_id
    assert claim.policy.starts_on <= scope.incident_date <= claim.policy.ends_on
    assert result["sources"][0]["text"] == "An invoice is required."


@pytest.mark.parametrize(
    "response",
    [
        reply(ids=["foreign-source"]),
        reply(answer="Unsupported statement. [P999]"),
        reply(answer=""),
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"refusal": "No"}}]},
    ],
)
def test_invalid_or_truncated_chat_answer_fails_closed(claim, response):
    with (
        patch("verification.chat.retrieve", return_value=[]),
        patch("verification.chat.chat_completion", return_value=response),
    ):
        with pytest.raises(RagError, match="invalid_chat_answer"):
            answer_question(
                customer=claim.customer, question="Status?", history=[], claim=claim
            )


def test_missing_wording_is_explicit_and_disabled_rag_never_calls_provider(
    claim, settings
):
    with (
        patch(
            "verification.chat.retrieve",
            side_effect=RagError("policy_knowledge_missing_or_ambiguous"),
        ),
        patch("verification.chat.chat_completion", return_value=reply()) as provider,
    ):
        result = answer_question(
            customer=claim.customer, question="Status?", history=[], claim=claim
        )
        assert result["notice"]
        assert (
            "not available"
            in json.loads(provider.call_args.args[0]["messages"][1]["content"])[
                "wording_availability"
            ]
        )
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "enabled": False}
    with patch("verification.chat.chat_completion") as provider:
        with pytest.raises(RagError, match="rag_disabled"):
            answer_question(
                customer=claim.customer, question="Status?", history=[], claim=claim
            )
    provider.assert_not_called()


def test_rate_limit_errors_preserve_question_and_do_not_save_answer(claim):
    client = Client()
    client.force_login(claim.customer)
    with patch(
        "common.chat.answer_question", side_effect=RagError("provider_rate_limited")
    ):
        response = client.post(
            "/accounts/chat/",
            {"context": f"claim:{claim.pk}", "question": "Why rejected?"},
        )
    assert response.status_code == 503
    assert b"rate limit" in response.content
    assert b"Why rejected?" in response.content
    assert "customer_chat_" + str(claim.customer_id) not in client.session
    cache.set(f"customer-chat-rate:{claim.customer_id}", 10, 300)
    with patch("common.chat.answer_question") as provider:
        assert (
            client.post(
                "/accounts/chat/",
                {"context": f"claim:{claim.pk}", "question": "Status?"},
            ).status_code
            == 429
        )
    provider.assert_not_called()


@pytest.mark.parametrize("question", ["Hi!", "hello", "Thanks", "okay"])
def test_conversational_messages_do_not_require_citations_or_api(claim, question):
    with (
        patch("verification.chat.retrieve") as retrieve,
        patch("verification.chat.chat_completion") as provider,
    ):
        result = answer_question(
            customer=claim.customer, question=question, history=[], claim=claim
        )
    assert result["answer"]
    assert result["sources"] == []
    retrieve.assert_not_called()
    provider.assert_not_called()


def test_invalid_chat_output_is_repaired_once_without_logging_text(claim, caplog):
    with (
        patch("verification.chat.retrieve", return_value=[]),
        patch(
            "verification.chat.chat_completion",
            side_effect=[reply("PRIVATE ANSWER [P999]", ["C1"]), reply()],
        ) as provider,
    ):
        result = answer_question(
            customer=claim.customer,
            question="PRIVATE QUESTION",
            history=[],
            claim=claim,
        )
    assert provider.call_count == 2
    assert result["sources"][0]["id"] == "C1"
    assert "inline_citation_mismatch" in caplog.text
    assert "PRIVATE ANSWER" not in caplog.text
    assert "PRIVATE QUESTION" not in caplog.text


def test_followup_strips_old_source_aliases_and_uses_current_sources(claim):
    history = [
        {"question": "What documents?", "answer": "Old requirement [P999, D999]."}
    ]
    with (
        patch("verification.chat.retrieve", return_value=[]) as retrieve,
        patch("verification.chat.chat_completion", return_value=reply()) as provider,
    ):
        answer_question(
            customer=claim.customer,
            question="What about that?",
            history=history,
            claim=claim,
        )
    data = json.loads(provider.call_args.args[0]["messages"][1]["content"])
    assert "P999" not in data["recent_conversation"][0]["answer"]
    assert "D999" not in data["recent_conversation"][0]["answer"]
    assert "What documents?" in retrieve.call_args.args[1]
    assert "What about that?" in retrieve.call_args.args[1]


@pytest.mark.parametrize(
    "response,reason",
    [
        (reply(ids=[]), "missing_citations"),
        (reply(ids=["C1"] * 9), "too_many_citations"),
        (reply("Unsupported [C1, P999]", ["C1"]), "inline_citation_mismatch"),
    ],
)
def test_chat_validation_reasons_are_specific(response, reason):
    from verification.chat_validation import validate_chat_response

    with pytest.raises(RagError) as caught:
        validate_chat_response(response, ["C1"])
    assert caught.value.detail == reason


def test_unrepairable_chat_fails_after_two_calls_and_preserves_draft(claim):
    client = Client()
    client.force_login(claim.customer)
    with (
        patch("verification.chat.retrieve", return_value=[]),
        patch(
            "verification.chat.chat_completion", return_value=reply(ids=[])
        ) as provider,
    ):
        response = client.post(
            "/accounts/chat/",
            {"context": f"claim:{claim.pk}", "question": "Why rejected?"},
        )
    assert provider.call_count == 2
    assert response.status_code == 503
    assert b"supporting source references" in response.content
    assert b"Why rejected?" in response.content
    assert "customer_chat_" + str(claim.customer_id) not in client.session


def test_chat_refusal_is_not_retried(claim):
    with (
        patch("verification.chat.retrieve", return_value=[]),
        patch(
            "verification.chat.chat_completion",
            return_value={
                "choices": [
                    {"finish_reason": "stop", "message": {"refusal": "declined"}}
                ]
            },
        ) as provider,
    ):
        with pytest.raises(RagError) as caught:
            answer_question(
                customer=claim.customer, question="Status?", history=[], claim=claim
            )
    assert provider.call_count == 1
    assert caught.value.detail == "refusal"
