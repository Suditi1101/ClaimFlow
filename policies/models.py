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
