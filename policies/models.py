from django.db import models
from django.conf import settings


class Policy(models.Model):
    reference = models.CharField(max_length=60, unique=True)
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
