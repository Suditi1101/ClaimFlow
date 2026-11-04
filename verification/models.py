from django.db import models


class CheckResult(models.Model):
    claim = models.ForeignKey(
        "claims.Claim", on_delete=models.CASCADE, related_name="checks"
    )
    kind = models.CharField(max_length=20)
    outcome = models.CharField(max_length=20)
    reason = models.CharField(max_length=200)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["claim", "kind"], name="unique_claim_check")
        ]
