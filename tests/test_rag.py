from django.utils import timezone
import hashlib
import io
import json
import subprocess
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.db import connection
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from claims.models import Claim, Document, ClaimDocument
from common.models import Outbox, ProcessedEvent
from common.processing import process
from policies.models import Policy
from verification.models import (
    PolicyKnowledge,
    PolicyChunk,
    ClaimAssessment,
    CheckResult,
)
from verification.rag import (
    extract_pdf,
    ingest_policy,
    retrieve,
    generate,
    verify_claim,
    assess,
)
from verification.rag_clients import RagError, embed, request_json

pytestmark = pytest.mark.django_db


def pdf(
    text="Hospital invoice admission dates and itemized treatment charges are required.",
):
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=500)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 10 450 Td (" + text + ") Tj ET").encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(autouse=True)
def rag_settings(settings):
    settings.CLAIM_RAG = {
        **settings.CLAIM_RAG,
        "enabled": True,
        "groq_api_key": "synthetic-groq-key",
        "gemini_api_key": "synthetic-gemini-key",
        "chat_model": "test-model",
    }


@pytest.fixture
def claim():
    user = get_user_model().objects.create_user("rag-owner")
    policy = Policy.objects.create(
        customer=user,
        starts_on=timezone.localdate() - timedelta(days=30),
        ends_on=timezone.localdate() + timedelta(days=30),
        coverage_limit=1000,
    )
    claim = Claim.objects.create(
        customer=user,
        policy=policy,
        amount=100,
        incident_date=timezone.localdate(),
        description="Hospital expenses for treatment",
        idempotency_key="rag-1",
        request_hash="x" * 64,
    )
    data = pdf()
    doc = Document.objects.create(
        owner=user,
        file=SimpleUploadedFile("invoice.pdf", data),
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    ClaimDocument.objects.create(claim=claim, document=doc)
    return claim


@pytest.fixture
def knowledge(claim):
    k = PolicyKnowledge.objects.create(
        policy=claim.policy,
        version="v1",
        title="Hospital coverage",
        effective_from=claim.policy.starts_on,
        effective_to=claim.policy.ends_on,
        sha256="a" * 64,
        embedding_model=settings.CLAIM_RAG["embedding_model"],
        collection=settings.CLAIM_RAG["collection"],
        ready=True,
    )
    PolicyChunk.objects.create(
        knowledge=k,
        page=1,
        position=0,
        text="Hospital claims require an itemized invoice and discharge summary.",
    )
    return k


def model_response(sources, evidence, invalid=False):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "summary": "The itemized invoice needs staff review.",
                            "findings": [
                                {
                                    "outcome": "concern",
                                    "explanation": "Confirm that the invoice includes each treatment charge.",
                                    "policy_ids": [
                                        "invented" if invalid else sources[0]["id"]
                                    ],
                                    "evidence_ids": [evidence[0]["id"]],
                                }
                            ],
                        }
                    )
                },
            }
        ]
    }


def fake_services(knowledge, invalid=False):
    chunk = knowledge.chunks.get()

    def provider(payload):
        content = json.loads(payload["messages"][1]["content"])
        return model_response(
            content["policy_sources"], content["claim_evidence"], invalid
        )

    return (
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch(
            "verification.rag.qdrant",
            return_value={"result": {"points": [{"id": str(chunk.pk)}]}},
        ),
        patch("verification.rag.chat_completion", side_effect=provider),
    )


def test_real_pdf_extraction_and_blank_rejection():
    assert "Hospital invoice" in extract_pdf(pdf())[0]["text"]
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(RagError, match="manual_extraction"):
        extract_pdf(output.getvalue())
    with patch(
        "verification.rag.subprocess.run",
        side_effect=subprocess.TimeoutExpired("extract", 15),
    ):
        with pytest.raises(RagError, match="manual_extraction"):
            extract_pdf(pdf())


def test_ingestion_is_idempotent_and_immutable(claim):
    data = pdf()
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch("verification.rag.qdrant") as vector,
    ):
        first = ingest_policy(
            claim.policy,
            data,
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        )
        second = ingest_policy(
            claim.policy,
            data,
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        )
        assert first.pk == second.pk and PolicyKnowledge.objects.count() == 1
        assert vector.call_args_list[0].args[2] == vector.call_args_list[1].args[2]
        with pytest.raises(RagError, match="immutable"):
            ingest_policy(
                claim.policy,
                pdf(
                    "Different policy wording for hospital claim document requirements."
                ),
                "v1",
                "Coverage",
                claim.policy.starts_on,
                claim.policy.ends_on,
            )


def test_failed_index_is_not_ready_and_can_resume(claim):
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", side_effect=RagError("provider_unavailable")),
    ):
        with pytest.raises(RagError):
            ingest_policy(
                claim.policy,
                pdf(),
                "v1",
                "Coverage",
                claim.policy.starts_on,
                claim.policy.ends_on,
            )
    assert not PolicyKnowledge.objects.get().ready
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch("verification.rag.qdrant"),
    ):
        assert ingest_policy(
            claim.policy,
            pdf(),
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        ).ready
    assert PolicyKnowledge.objects.count() == 1


def test_retrieval_filters_and_rejects_foreign_chunks(claim, knowledge):
    with (
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch(
            "verification.rag.qdrant",
            return_value={"result": {"points": [{"id": str(uuid.uuid4())}]}},
        ) as query,
    ):
        with pytest.raises(RagError, match="retrieval_evidence_unavailable"):
            retrieve(claim, "hospital")
        filters = query.call_args.args[2]["filter"]["must"]
        assert filters[0]["match"]["value"] == str(knowledge.pk)
        assert filters[1]["match"]["value"] == str(claim.policy_id)
    knowledge.effective_to = timezone.localdate() - timedelta(days=1)
    knowledge.save()
    with pytest.raises(RagError, match="missing_or_ambiguous"):
        retrieve(claim, "hospital")


def test_worker_persists_citations_and_retry_is_safe(claim, knowledge):
    a, b, c = fake_services(knowledge)
    event = {
        "id": str(uuid.uuid4()),
        "version": 1,
        "type": "ClaimSubmitted",
        "claim_id": str(claim.pk),
        "data": {},
    }
    with a, b, c as provider:
        process("document", event)
        process("document", event)
        assert provider.call_count == 1
    assessment = ClaimAssessment.objects.get()
    assert assessment.status == "completed" and assessment.findings
    assert assessment.policy_sources[0]["version"] == "v1"
    assert assessment.evidence[0]["document_id"] == str(
        claim.document_links.get().document_id
    )
    assert CheckResult.objects.get().outcome == "review"
    assert Outbox.objects.filter(kind="CheckCompleted").count() == 1
    assert ProcessedEvent.objects.count() == 1
    claim.refresh_from_db()
    assert claim.status == "submitted"


def test_invalid_citations_route_to_review(claim, knowledge):
    a, b, c = fake_services(knowledge, invalid=True)
    with a, b, c:
        result = verify_claim(claim.pk)
    assert (
        result.status == "needs_review"
        and result.error_code == "invalid_model_findings"
    )
    assert result.findings == []
    assert CheckResult.objects.get().outcome == "review"


def test_missing_knowledge_and_corrupt_files_do_not_pass(claim):
    assert assess(claim)["error_code"] == "policy_knowledge_missing_or_ambiguous"
    doc = claim.document_links.get().document
    doc.sha256 = "f" * 64
    doc.save()
    assert assess(claim)["error_code"] == "document_checksum_mismatch"


def test_provider_failure_is_safe_and_logs_no_medical_text(claim, knowledge, caplog):
    with patch(
        "verification.rag.retrieve", side_effect=RagError("provider_unavailable")
    ):
        assessment = verify_claim(claim.pk)
    assert assessment.status == "needs_review"
    assert "synthetic-key" not in caplog.text and "Hospital invoice" not in caplog.text
    assert CheckResult.objects.get().outcome == "review"


def test_disabled_pipeline_never_calls_external_provider(claim, settings):
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "enabled": False}
    with patch("verification.rag.chat_completion") as provider:
        assert assess(claim)["error_code"] == "rag_disabled"
        provider.assert_not_called()


def test_api_is_owned_and_does_not_expose_medical_text(claim):
    ClaimAssessment.objects.create(
        claim=claim,
        status="completed",
        summary="Internal medical finding",
        evidence=[{"text": "private diagnosis"}],
    )
    client = Client()
    client.force_login(claim.customer)
    response = client.get(f"/api/claims/{claim.pk}")
    assert response.status_code == 200
    assert response.json()["rag_verification"]["staff_review_required"]
    assert (
        b"private diagnosis" not in response.content
        and b"Internal medical" not in response.content
    )
    client.force_login(get_user_model().objects.create_user("different-owner"))
    assert client.get(f"/api/claims/{claim.pk}").status_code == 404


def test_final_decision_during_assessment_is_preserved(claim):
    def finish(_):
        Claim.objects.filter(pk=claim.pk).update(status="rejected")
        return {"status": "completed", "summary": "advisory"}

    with patch("verification.rag.assess", side_effect=finish):
        with pytest.raises(RagError, match="finalized"):
            verify_claim(claim.pk)
    assert not ClaimAssessment.objects.exists()


def test_model_refusal_is_rejected(claim):
    with patch(
        "verification.rag.chat_completion",
        return_value={
            "choices": [
                {"finish_reason": "stop", "message": {"refusal": "cannot process"}}
            ]
        },
    ):
        with pytest.raises(RagError, match="invalid_model_findings"):
            generate(claim, [], [])


def test_embedding_and_http_errors_are_sanitized():
    with patch(
        "verification.rag_clients.request_json",
        return_value={"embeddings": [{"values": [1]}]},
    ):
        with pytest.raises(RagError, match="invalid_embeddings"):
            embed(["coverage"])
    response = Mock(status_code=429, content=b"secret error body")
    with patch("verification.rag_clients.requests.request", return_value=response):
        with pytest.raises(RagError, match="provider_unavailable"):
            request_json("GET", "https://example.invalid")


def test_assessment_calls_provider_outside_transaction(claim):
    # The test fixture owns one transaction; assess itself must not add another.
    depth = len(connection.atomic_blocks)

    def retrieve_without_transaction(*_):
        assert len(connection.atomic_blocks) == depth
        raise RagError("provider_unavailable")

    with patch(
        "verification.rag.retrieve", side_effect=retrieve_without_transaction
    ) as retrieve_mock:
        verify_claim(claim.pk)
    assert retrieve_mock.called and len(connection.atomic_blocks) == depth


def test_storage_backend_error_routes_to_review(claim):
    with patch(
        "django.db.models.fields.files.FieldFile.open",
        side_effect=RuntimeError("storage backend error"),
    ):
        result = verify_claim(claim.pk)
    assert (
        result.status == "needs_review"
        and result.error_code == "document_storage_unavailable"
    )


def test_invalid_configuration_is_manual_review(claim, settings):
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "chat_model": "x" * 200}
    result = verify_claim(claim.pk)
    assert result.error_code == "invalid_model_configuration"
    assert len(result.model) <= 100


def test_ambiguous_versions_require_review(claim, knowledge):
    PolicyKnowledge.objects.create(
        policy=claim.policy,
        version="v2",
        title="Overlapping wording",
        effective_from=knowledge.effective_from,
        effective_to=knowledge.effective_to,
        sha256="b" * 64,
        embedding_model=knowledge.embedding_model,
        collection=knowledge.collection,
        ready=True,
    )
    with pytest.raises(RagError, match="missing_or_ambiguous"):
        retrieve(claim, "hospital")


def test_generation_rejects_foreign_evidence_reference(claim):
    sources = [{"id": "authorized-policy-chunk"}]
    evidence = [{"id": "authorized-document-page"}]
    response = model_response(sources, [{"id": "other-customer-document"}])
    with patch("verification.rag.chat_completion", return_value=response):
        with pytest.raises(RagError, match="invalid_model_findings"):
            generate(claim, evidence, sources)


def test_view_only_staff_cannot_read_rag_medical_evidence(claim):
    from django.contrib.auth.models import Permission

    ClaimAssessment.objects.create(
        claim=claim,
        status="completed",
        summary="Private medical result",
        evidence=[{"text": "private diagnosis"}],
    )
    staff = get_user_model().objects.create_user("view-only-staff", is_staff=True)
    staff.user_permissions.add(
        Permission.objects.get(codename="view_claim"),
        Permission.objects.get(codename="view_claimassessment"),
    )
    client = Client()
    client.force_login(staff)
    page = client.get(f"/admin/claims/claim/{claim.pk}/change/")
    assert page.status_code == 200 and b"private diagnosis" not in page.content
    assert client.get("/admin/verification/claimassessment/").status_code == 403


def test_embedding_migration_requires_separate_collection(claim, settings):
    data = pdf()
    old = {
        **settings.CLAIM_RAG,
        "embedding_model": "text-embedding-3-small",
        "collection": "old_openai_index",
    }
    settings.CLAIM_RAG = old
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch("verification.rag.qdrant"),
    ):
        original = ingest_policy(
            claim.policy,
            data,
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        )
    ids = list(original.chunks.values_list("id", flat=True))
    settings.CLAIM_RAG = {**old, "embedding_model": "gemini-embedding-001"}
    with pytest.raises(RagError, match="new_collection"):
        ingest_policy(
            claim.policy,
            data,
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        )
    settings.CLAIM_RAG = {**settings.CLAIM_RAG, "collection": "new_gemini_index"}
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", side_effect=RagError("provider_unavailable")),
    ):
        with pytest.raises(RagError):
            ingest_policy(
                claim.policy,
                data,
                "v1",
                "Coverage",
                claim.policy.starts_on,
                claim.policy.ends_on,
            )
    original.refresh_from_db()
    assert original.collection == "old_openai_index"
    with (
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch("verification.rag.qdrant"),
    ):
        migrated = ingest_policy(
            claim.policy,
            data,
            "v1",
            "Coverage",
            claim.policy.starts_on,
            claim.policy.ends_on,
        )
    assert (
        migrated.pk == original.pk
        and list(migrated.chunks.values_list("id", flat=True)) == ids
    )
    assert (
        migrated.collection == "new_gemini_index"
        and migrated.embedding_model == "gemini-embedding-001"
    )
