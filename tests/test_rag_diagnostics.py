from tests.test_rag import claim as claim_fixture, rag_settings  # noqa: F401
import json
from unittest.mock import patch

import pytest

from tests.test_rag import model_response
from verification.models import ClaimAssessment
from verification.rag import generate, verify_claim
from verification.rag_clients import RagError
from verification.rag_validation import validate_response

pytestmark = pytest.mark.django_db
claim = claim_fixture


def test_uncited_concern_is_repaired_once(claim):
    evidence, sources = [{"id": "doc-1"}], [{"id": "policy-1"}]
    bad = model_response(sources, evidence)
    content = json.loads(bad["choices"][0]["message"]["content"])
    content["findings"][0]["evidence_ids"] = []
    bad["choices"][0]["message"]["content"] = json.dumps(content)
    with pytest.raises(RagError) as caught:
        validate_response(bad, evidence, sources)
    assert caught.value.detail == "missing_evidence_citation"
    content["findings"][0]["outcome"] = "missing_information"
    good = model_response(sources, evidence)
    good["choices"][0]["message"]["content"] = json.dumps(content)
    with patch("verification.rag.chat_completion", side_effect=[bad, good]) as provider:
        result = generate(claim, evidence, sources)
    assert provider.call_count == 2
    assert result["findings"][0]["outcome"] == "missing_information"


def test_invalid_output_still_fails_after_repair(claim):
    evidence, sources = [{"id": "doc-1"}], [{"id": "policy-1"}]
    with patch(
        "verification.rag.chat_completion",
        return_value=model_response(sources, evidence, invalid=True),
    ) as provider:
        with pytest.raises(RagError) as caught:
            generate(claim, evidence, sources)
    assert caught.value.detail == "unknown_policy_citation"
    assert provider.call_count == 2


def test_refusal_is_not_retried(claim):
    with patch(
        "verification.rag.chat_completion",
        return_value={
            "choices": [{"finish_reason": "stop", "message": {"refusal": "declined"}}]
        },
    ) as provider:
        with pytest.raises(RagError) as caught:
            generate(claim, [], [])
    assert caught.value.detail == "refusal"
    assert provider.call_count == 1


def test_retry_only_failed_pending_assessment(claim):
    saved = ClaimAssessment.objects.create(
        claim=claim, status="needs_review", error_code="invalid_model_findings"
    )
    result = {
        "status": "completed",
        "summary": "Review invoice.",
        "findings": [],
        "error_code": "",
        "prompt_version": "claim-review-v2",
    }
    with patch("verification.rag.assess", return_value=result) as assess:
        retried = verify_claim(claim.pk, retry_failed=True)
        assert retried.pk == saved.pk
        assert retried.status == "completed"
        assert verify_claim(claim.pk).pk == saved.pk
        with pytest.raises(RagError, match="assessment_is_not_failed"):
            verify_claim(claim.pk, retry_failed=True)
    assert assess.call_count == 1


def test_retry_does_not_change_final_claim(claim):
    ClaimAssessment.objects.create(
        claim=claim, status="needs_review", error_code="invalid_model_findings"
    )
    claim.status = "rejected"
    claim.save(update_fields=["status"])
    with patch("verification.rag.assess") as assess:
        with pytest.raises(RagError, match="claim_already_finalized"):
            verify_claim(claim.pk, retry_failed=True)
    assess.assert_not_called()


@pytest.mark.parametrize(
    "status,code",
    [
        (429, "provider_rate_limited"),
        (401, "provider_auth_failed"),
        (403, "provider_auth_failed"),
    ],
)
def test_provider_http_failures_are_actionable(status, code):
    from unittest.mock import Mock
    from verification.rag_clients import request_json

    response = Mock(status_code=status, headers={})
    with patch("verification.rag_clients.requests.request", return_value=response):
        with pytest.raises(RagError, match=code):
            request_json("POST", "https://api.groq.com/openai/v1/chat/completions")
