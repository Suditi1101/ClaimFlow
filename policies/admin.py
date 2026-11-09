from django.contrib import admin
from policies.models import Policy


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "coverage_limit", "active")
    readonly_fields = ("reference",)

    def get_fields(self, request, obj=None):
        fields = ["customer", "starts_on", "ends_on", "coverage_limit", "active"]
        return ["reference", *fields] if obj is not None else fields
