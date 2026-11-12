from django.contrib import admin
from verification.models import PolicyKnowledge, ClaimAssessment


class ReadOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PolicyKnowledge)
class PolicyKnowledgeAdmin(ReadOnlyAdmin):
    list_display = ("policy", "version", "effective_from", "effective_to", "ready")
    list_filter = ("ready",)


@admin.register(ClaimAssessment)
class ClaimAssessmentAdmin(ReadOnlyAdmin):
    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(
            "claims.change_claim"
        ) and super().has_view_permission(request, obj)

    list_display = ("claim", "status", "error_code", "created_at")
