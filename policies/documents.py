"""Private uploads and durable, leased policy indexing jobs."""

import hashlib
import logging
import uuid
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db import connection, transaction, OperationalError, InterfaceError
from django.db.models import Q
from django.utils import timezone
from policies.models import Policy, PolicyDocument
from verification.models import PolicyKnowledge
from verification.rag import ingest_policy
from verification.rag_clients import RagError

log = logging.getLogger(__name__)


def validate_version(policy, version, effective_from, effective_to):
    if effective_to < effective_from:
        raise ValidationError("Effective-to must be on or after effective-from.")
    if (
        policy.documents.filter(version=version).exists()
        or policy.knowledge_versions.filter(version=version).exists()
    ):
        raise ValidationError(
            "This policy version already exists. Use a new version or retry its failed indexing job."
        )
    if (
        policy.documents.exclude(status="failed")
        .filter(effective_from__lte=effective_to, effective_to__gte=effective_from)
        .exists()
        or policy.knowledge_versions.filter(
            ready=True,
            effective_from__lte=effective_to,
            effective_to__gte=effective_from,
        ).exists()
    ):
        raise ValidationError(
            "The effective dates overlap an existing policy document. Use a non-overlapping version period."
        )


def upload_document(policy, uploader, data):
    if (
        not uploader.is_active
        or not uploader.is_staff
        or not uploader.has_perm("policies.change_policy")
    ):
        raise PermissionDenied
    file = data["file"]
    file.seek(0)
    if file.size > settings.MAX_DOCUMENT_BYTES or file.read(5) != b"%PDF-":
        raise ValidationError("Upload a PDF within the upload size limit.")
    file.seek(0)
    digest = hashlib.sha256()
    for chunk in file.chunks():
        digest.update(chunk)
    file.seek(0)
    validate_version(
        policy, data["version"], data["effective_from"], data["effective_to"]
    )
    document = PolicyDocument(
        policy=policy,
        uploaded_by=uploader,
        sha256=digest.hexdigest(),
        title=data["title"],
        version=data["version"],
        effective_from=data["effective_from"],
        effective_to=data["effective_to"],
    )
    # Complete external storage I/O before acquiring the database lock.
    try:
        document.file.save(uuid.uuid4().hex + ".pdf", file, save=False)
    except Exception as exc:
        raise OSError("policy_document_storage_unavailable") from exc
    try:
        with transaction.atomic():
            Policy.objects.select_for_update().get(pk=policy.pk)
            validate_version(
                policy, data["version"], data["effective_from"], data["effective_to"]
            )
            document.save()
    except Exception:
        try:
            document.file.delete(save=False)
        except Exception:
            log.error("policy_document_cleanup_failed policy_id=%s", policy.pk)
        raise
    log.info(
        "policy_document_queued policy_id=%s document_id=%s", policy.pk, document.pk
    )
    return document


def index_next_document():
    """Claim one job briefly; perform storage/provider I/O outside the transaction."""
    now = timezone.now()
    with transaction.atomic():
        jobs = PolicyDocument.objects.filter(
            Q(status="pending")
            | Q(status="processing", started_at__lt=now - timedelta(minutes=10))
        )
        jobs = jobs.select_for_update(
            skip_locked=connection.features.has_select_for_update_skip_locked
        )
        document = jobs.order_by("created_at").first()
        if document is None:
            return False
        token = uuid.uuid4()
        document.status = "processing"
        document.started_at = now
        document.processing_token = token
        document.error_code = ""
        document.save(
            update_fields=[
                "status",
                "started_at",
                "processing_token",
                "error_code",
                "updated_at",
            ]
        )
    try:
        try:
            with document.file.open("rb") as stream:
                data = stream.read(10 * 1024 * 1024 + 1)
        except Exception as exc:
            raise RagError("document_storage_unavailable") from exc
        if hashlib.sha256(data).hexdigest() != document.sha256:
            raise RagError("document_checksum_mismatch")
        if (
            PolicyKnowledge.objects.filter(
                policy=document.policy,
                ready=True,
                effective_from__lte=document.effective_to,
                effective_to__gte=document.effective_from,
            )
            .exclude(version=document.version)
            .exists()
        ):
            raise RagError("policy_version_dates_overlap")
        knowledge = ingest_policy(
            document.policy,
            data,
            document.version,
            document.title,
            document.effective_from,
            document.effective_to,
        )
    except (OperationalError, InterfaceError):
        raise
    except Exception as exc:
        code = str(exc) if isinstance(exc, RagError) else "policy_indexing_failed"
        PolicyDocument.objects.filter(pk=document.pk, processing_token=token).update(
            status="failed", error_code=code, updated_at=timezone.now()
        )
        log.warning(
            "policy_document_index_failed document_id=%s code=%s", document.pk, code
        )
    else:
        PolicyDocument.objects.filter(pk=document.pk, processing_token=token).update(
            status="ready",
            knowledge=knowledge,
            error_code="",
            updated_at=timezone.now(),
        )
        log.info("policy_document_indexed document_id=%s", document.pk)
    return True
