from django.db import models


class Notification(models.Model):
    """Durable in-app notification; no email/SMS adapter configured."""

    claim = models.ForeignKey("claims.Claim", on_delete=models.CASCADE)
    kind = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["claim", "kind"], name="unique_claim_notification"
            )
        ]
