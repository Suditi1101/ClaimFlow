import uuid
from django.db import models
from django.conf import settings


def generate_policy_reference():
    return f"POL-{uuid.uuid4().hex.upper()}"


class Policy(models.Model):
    reference = models.CharField(
        max_length=60,
        unique=True,
        default=generate_policy_reference,
        editable=False,
        help_text="Generated automatically when the policy is created.",
    )
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    starts_on = models.DateField()
    ends_on = models.DateField()
    coverage_limit = models.DecimalField(max_digits=12, decimal_places=2)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(coverage_limit__gt=0), name="positive_coverage"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="valid_policy_dates",
            ),
        ]

    def __str__(self):
        return self.reference


class PolicyDocument(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        Policy, on_delete=models.PROTECT, related_name="documents"
    )
    title = models.CharField(max_length=200)
    version = models.CharField(max_length=60)
    effective_from = models.DateField()
    effective_to = models.DateField()
    file = models.FileField(upload_to="private-policy-documents/%Y/%m/")
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    error_code = models.CharField(max_length=60, blank=True)
    knowledge = models.OneToOneField(
        "verification.PolicyKnowledge", null=True, blank=True, on_delete=models.PROTECT
    )
    processing_token = models.UUIDField(null=True, editable=False)
    started_at = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "version"], name="unique_policy_document_version"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__gte=models.F("effective_from")),
                name="valid_policy_document_dates",
            ),
        ]

    @property
    def needs_reindex(self):
        return bool(
            self.knowledge_id
            and (
                self.knowledge.embedding_model != settings.CLAIM_RAG["embedding_model"]
                or self.knowledge.collection != settings.CLAIM_RAG["collection"]
            )
        )

    @property
    def indexing_message(self):
        if self.status == self.Status.PENDING:
            return "Waiting for the policy-indexer worker. Refresh this page to check progress."
        if self.status == self.Status.PROCESSING:
            return "Extracting policy text and indexing it for retrieval."
        if self.status == self.Status.READY and self.needs_reindex:
            return "Embedding configuration changed. Re-index this document before using it with RAG."
        if self.status == self.Status.READY:
            return "Available to RAG for claims within these effective dates."
        return {
            "rag_not_configured": "Configure the Groq and Gemini API keys, then retry.",
            "groq_not_configured": "Configure GROQ_API_KEY for claim verification.",
            "gemini_not_configured": "Configure GEMINI_API_KEY, then retry policy indexing.",
            "embedding_change_requires_new_collection": "Set a new RAG_COLLECTION for the new embedding model, then retry.",
            "provider_unavailable": "The embedding or vector service is unavailable. Check service configuration, then retry.",
            "pdf_requires_manual_extraction": "This PDF could not be extracted. Upload a readable text PDF under a new version.",
            "invalid_pdf": "Upload a valid text PDF under a new version.",
            "document_storage_unavailable": "Document storage is unavailable. Restore storage access, then retry.",
            "document_checksum_mismatch": "Stored content no longer matches the upload. Upload the approved PDF under a new version.",
            "policy_version_dates_overlap": "Another ready policy version covers these dates. Resolve the version periods before retrying.",
        }.get(
            self.error_code,
            "Indexing failed. Check the RAG configuration and worker logs before retrying.",
        )

    def __str__(self):
        return f"{self.title} ({self.version})"
