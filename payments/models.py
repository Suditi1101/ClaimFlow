from django.db import models


class Payment(models.Model):
    claim = models.OneToOneField("claims.Claim", on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default="pending")
    provider_reference = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class ProviderReceipt(models.Model):
    """DEMO provider ledger. No real money movement."""

    key = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100)
