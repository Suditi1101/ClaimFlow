import uuid
from django.db import models
from django.conf import settings


class Claim(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        REVIEW = "manual_review"
        APPROVED = "approved"
        REJECTED = "rejected"
        PAID = "paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    policy = models.ForeignKey("policies.Policy", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    incident_date = models.DateField()
    description = models.TextField(max_length=4000)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True
    )
    idempotency_key = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "idempotency_key"], name="unique_customer_request"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="positive_claim_amount"
            ),
        ]


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    file = models.FileField(upload_to="private-documents/%Y/%m/")
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()


class ClaimDocument(models.Model):
    claim = models.ForeignKey(
        Claim, on_delete=models.CASCADE, related_name="document_links"
    )
    document = models.OneToOneField(Document, on_delete=models.PROTECT)


class ReviewDecision(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=20)
    reason = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
