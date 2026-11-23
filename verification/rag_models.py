import uuid
from django.db import models


class PolicyKnowledge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(
        "policies.Policy", on_delete=models.PROTECT, related_name="knowledge_versions"
    )
    version = models.CharField(max_length=60)
    title = models.CharField(max_length=200)
    effective_from = models.DateField()
    effective_to = models.DateField()
    sha256 = models.CharField(max_length=64)
    embedding_model = models.CharField(max_length=100)
    collection = models.CharField(max_length=100)
    ready = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "version"], name="unique_policy_knowledge_version"
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__gte=models.F("effective_from")),
                name="valid_knowledge_dates",
            ),
        ]

    def __str__(self):
        return f"{self.policy} / {self.version}"


class PolicyChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    knowledge = models.ForeignKey(
        PolicyKnowledge, on_delete=models.CASCADE, related_name="chunks"
    )
    page = models.PositiveIntegerField()
    position = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["knowledge", "position"], name="unique_knowledge_chunk"
            )
        ]
        ordering = ["position"]


class ClaimAssessment(models.Model):
    claim = models.OneToOneField(
        "claims.Claim", on_delete=models.CASCADE, related_name="rag_assessment"
    )
    status = models.CharField(max_length=20)  # completed / needs_review
    summary = models.CharField(max_length=1000)
    findings = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    policy_sources = models.JSONField(default=list)
    error_code = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=20, default="claim-review-v1")
    created_at = models.DateTimeField(auto_now_add=True)
