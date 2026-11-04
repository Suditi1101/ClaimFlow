from django.contrib import admin, messages
from rest_framework.exceptions import APIException
from claims.models import Claim, ReviewDecision, ClaimDocument
from django.utils.html import format_html
from claims.services import review


class DocumentInline(admin.TabularInline):
    model = ClaimDocument
    extra = 0
    readonly_fields = ("document", "download")

    def has_add_permission(self, r, obj=None):
        return False

    def has_delete_permission(self, r, obj=None):
        return False

    def download(self, obj):
        return format_html(
            '<a href="/api/documents/{}">Download PDF</a>', obj.document_id
        )


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "amount", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = tuple(f.name for f in Claim._meta.fields)
    actions = ["approve", "reject"]
    inlines = [DocumentInline]

    def has_add_permission(self, r):
        return False

    def has_delete_permission(self, r, obj=None):
        return False

    @admin.action(description="Approve claims awaiting review", permissions=["change"])
    def approve(self, r, q):
        self.decide(r, q, "approved")

    @admin.action(description="Reject claims awaiting review", permissions=["change"])
    def reject(self, r, q):
        self.decide(r, q, "rejected")

    def decide(self, r, q, decision):
        for claim in q:
            try:
                review(
                    claim.pk, r.user, decision, "Manual decision through Django Admin"
                )
            except APIException as exc:
                self.message_user(r, str(exc.detail), messages.ERROR)


@admin.register(ReviewDecision)
class ReviewAdmin(admin.ModelAdmin):
    readonly_fields = tuple(f.name for f in ReviewDecision._meta.fields)

    def has_add_permission(self, r):
        return False

    def has_change_permission(self, r, obj=None):
        return False

    def has_delete_permission(self, r, obj=None):
        return False
