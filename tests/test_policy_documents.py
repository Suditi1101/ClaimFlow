from datetime import timedelta
import uuid
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.utils import timezone
from policies.models import Policy, PolicyDocument
from policies.documents import index_next_document
from verification.models import PolicyKnowledge
from verification.rag_clients import RagError

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy():
    customer = get_user_model().objects.create_user("policy-doc-customer")
    return Policy.objects.create(
        customer=customer,
        starts_on=timezone.localdate(),
        ends_on=timezone.localdate() + timedelta(days=100),
        coverage_limit=1000,
    )


@pytest.fixture
def admin_client():
    user = get_user_model().objects.create_superuser("policy-doc-admin")
    client = Client()
    client.force_login(user)
    return client


def upload_url(policy):
    return f"/admin/policies/policy/{policy.pk}/documents/upload/"


def payload(policy, **overrides):
    return {
        "title": "Hospital coverage",
        "version": "v1",
        "effective_from": policy.starts_on.isoformat(),
        "effective_to": policy.ends_on.isoformat(),
        "file": SimpleUploadedFile("wording.pdf", b"%PDF-1.4 insurer wording"),
        **overrides,
    }


def upload(client, policy):
    assert client.post(upload_url(policy), payload(policy)).status_code == 302
    return policy.documents.get()


def test_admin_upload_section_and_private_download(policy, admin_client):
    page = admin_client.get(f"/admin/policies/policy/{policy.pk}/change/")
    assert b"Policy Documents" in page.content and b"Upload and Index" in page.content
    with patch("policies.documents.ingest_policy") as ingest:
        document = upload(admin_client, policy)
        ingest.assert_not_called()
    assert document.status == "pending" and document.uploaded_by.is_superuser
    assert document.file.name.startswith("private-policy-documents/")
    page = admin_client.get(f"/admin/policies/policy/{policy.pk}/change/")
    assert b"Hospital coverage" in page.content and b"Pending" in page.content
    result = admin_client.get(
        f"/admin/policies/policy/{policy.pk}/documents/{document.pk}/download/"
    )
    assert result.status_code == 200 and b"insurer wording" in b"".join(
        result.streaming_content
    )


def test_policy_change_permission_required(policy, admin_client):
    document = upload(admin_client, policy)
    client = Client()
    client.force_login(policy.customer)
    assert (
        client.post(upload_url(policy), payload(policy, version="v2")).status_code
        == 302
    )
    staff = get_user_model().objects.create_user("view-policy-only", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="view_policy"))
    client.force_login(staff)
    assert client.get(upload_url(policy)).status_code == 403
    assert (
        client.get(
            f"/admin/policies/policy/{policy.pk}/documents/{document.pk}/download/"
        ).status_code
        == 403
    )
    assert (
        b"Hospital coverage"
        not in client.get(f"/admin/policies/policy/{policy.pk}/change/").content
    )
    assert PolicyDocument.objects.count() == 1


def test_staff_with_change_policy_can_upload(policy):
    staff = get_user_model().objects.create_user("policy-editor", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(codename="change_policy"))
    client = Client()
    client.force_login(staff)
    assert client.post(upload_url(policy), payload(policy)).status_code == 302
    assert policy.documents.get().uploaded_by == staff


@pytest.mark.parametrize(
    "overrides",
    [
        {"file": SimpleUploadedFile("bad.pdf", b"not a PDF")},
        {"effective_from": "2026-12-31", "effective_to": "2026-01-01"},
        {"title": ""},
    ],
)
def test_invalid_upload_rejected(policy, admin_client, overrides):
    assert (
        admin_client.post(upload_url(policy), payload(policy, **overrides)).status_code
        == 200
    )
    assert not PolicyDocument.objects.exists()


def test_duplicate_and_overlapping_versions_rejected(policy, admin_client):
    upload(admin_client, policy)
    assert admin_client.post(upload_url(policy), payload(policy)).status_code == 200
    response = admin_client.post(upload_url(policy), payload(policy, version="v2"))
    assert response.status_code == 200 and b"overlap" in response.content
    assert PolicyDocument.objects.count() == 1


def test_oversized_upload_and_csrf(policy, admin_client):
    with override_settings(MAX_DOCUMENT_BYTES=10):
        assert admin_client.post(upload_url(policy), payload(policy)).status_code == 200
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(get_user_model().objects.get(username="policy-doc-admin"))
    assert protected.post(upload_url(policy), payload(policy)).status_code == 403
    assert not PolicyDocument.objects.exists()


def test_document_id_is_scoped_to_policy(policy, admin_client):
    document = upload(admin_client, policy)
    other = Policy.objects.create(
        customer=policy.customer,
        starts_on=policy.starts_on,
        ends_on=policy.ends_on,
        coverage_limit=1000,
    )
    assert (
        admin_client.get(
            f"/admin/policies/policy/{other.pk}/documents/{document.pk}/download/"
        ).status_code
        == 404
    )


def test_background_index_ready_without_long_transaction(policy, admin_client):
    document = upload(admin_client, policy)
    depth = len(connection.atomic_blocks)

    def ingest(*args):
        assert len(connection.atomic_blocks) == depth
        assert PolicyDocument.objects.get().status == "processing"
        return PolicyKnowledge.objects.create(
            policy=policy,
            title=document.title,
            version=document.version,
            effective_from=document.effective_from,
            effective_to=document.effective_to,
            sha256=document.sha256,
            embedding_model="test-embedding",
            collection="test",
            ready=True,
        )

    with patch("policies.documents.ingest_policy", side_effect=ingest) as mocked:
        assert index_next_document()
        assert not index_next_document()
        assert mocked.call_count == 1
    document.refresh_from_db()
    assert document.status == "ready" and document.knowledge.ready


def test_failure_and_post_only_retry(policy, admin_client, caplog):
    document = upload(admin_client, policy)
    with patch(
        "policies.documents.ingest_policy", side_effect=RagError("provider_unavailable")
    ):
        assert index_next_document()
    document.refresh_from_db()
    assert document.status == "failed" and document.error_code == "provider_unavailable"
    url = f"/admin/policies/policy/{policy.pk}/documents/{document.pk}/retry/"
    assert admin_client.get(url).status_code == 200
    document.refresh_from_db()
    assert document.status == "failed"
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(document.uploaded_by)
    assert protected.post(url).status_code == 403
    assert admin_client.post(url).status_code == 302
    document.refresh_from_db()
    assert document.status == "pending"
    assert "insurer wording" not in caplog.text


def test_stale_processing_job_is_recovered(policy, admin_client):
    document = upload(admin_client, policy)
    old_token = uuid.uuid4()
    PolicyDocument.objects.filter(pk=document.pk).update(
        status="processing",
        started_at=timezone.now() - timedelta(minutes=11),
        processing_token=old_token,
    )
    with patch(
        "policies.documents.ingest_policy", side_effect=RagError("provider_unavailable")
    ):
        assert index_next_document()
    document.refresh_from_db()
    assert document.status == "failed" and document.processing_token != old_token


def test_integrity_failure_never_indexes(policy, admin_client):
    document = upload(admin_client, policy)
    PolicyDocument.objects.filter(pk=document.pk).update(sha256="f" * 64)
    with patch("policies.documents.ingest_policy") as ingest:
        assert index_next_document()
        ingest.assert_not_called()
    document.refresh_from_db()
    assert document.error_code == "document_checksum_mismatch"


def test_unknown_backend_failure_is_sanitized(policy, admin_client):
    document = upload(admin_client, policy)
    with patch(
        "policies.documents.ingest_policy",
        side_effect=RuntimeError("secret provider error"),
    ):
        assert index_next_document()
    document.refresh_from_db()
    assert (
        document.status == "failed" and document.error_code == "policy_indexing_failed"
    )


def test_service_cleans_storage_after_validation_race(policy, admin_client):
    from django.core.exceptions import ValidationError
    from policies.documents import upload_document
    from django.core.files.storage import default_storage

    data = payload(policy)
    data["effective_from"] = policy.starts_on
    data["effective_to"] = policy.ends_on
    user = get_user_model().objects.get(username="policy-doc-admin")
    with (
        patch(
            "policies.documents.validate_version",
            side_effect=[None, ValidationError("duplicate")],
        ),
        patch.object(default_storage, "delete", wraps=default_storage.delete) as delete,
    ):
        with pytest.raises(ValidationError):
            upload_document(policy, user, data)
        delete.assert_called_once()
    assert not PolicyDocument.objects.exists()


def test_admin_pdf_reaches_real_rag_ingestion(policy, admin_client):
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from django.conf import settings

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
    stream.set_data(
        b"BT /F1 12 Tf 10 450 Td (Hospital coverage requires an itemized bill and discharge summary.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    response = admin_client.post(
        upload_url(policy),
        payload(policy, file=SimpleUploadedFile("wording.pdf", output.getvalue())),
    )
    assert response.status_code == 302
    with (
        override_settings(
            CLAIM_RAG={
                **settings.CLAIM_RAG,
                "groq_api_key": "synthetic-groq-key",
                "gemini_api_key": "synthetic-gemini-key",
                "chat_model": "test-model",
            }
        ),
        patch("verification.rag.ensure_collection"),
        patch("verification.rag.embed", return_value=[[0.1] * 1536]),
        patch("verification.rag.qdrant") as vector,
    ):
        assert index_next_document()
    document = policy.documents.get()
    assert document.status == "ready" and document.knowledge.ready
    assert document.knowledge.chunks.get().page == 1
    assert "itemized bill" in document.knowledge.chunks.get().text
    assert vector.call_args.args[1] == "/points?wait=true"


def test_admin_can_reindex_ready_policy_after_provider_change(policy, admin_client):
    document = upload(admin_client, policy)
    knowledge = PolicyKnowledge.objects.create(
        policy=policy,
        version=document.version,
        title=document.title,
        effective_from=policy.starts_on,
        effective_to=policy.ends_on,
        sha256=document.sha256,
        embedding_model="text-embedding-3-small",
        collection="old_openai_index",
        ready=True,
    )
    document.knowledge = knowledge
    document.status = "ready"
    document.save()
    assert document.needs_reindex
    page = admin_client.get(f"/admin/policies/policy/{policy.pk}/change/")
    assert b"Re-index with current model" in page.content
    assert (
        admin_client.post(
            f"/admin/policies/policy/{policy.pk}/documents/{document.pk}/retry/"
        ).status_code
        == 302
    )
    document.refresh_from_db()
    assert document.status == "pending"
