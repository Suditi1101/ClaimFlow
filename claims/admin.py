from django import forms
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.contrib import admin, messages
from rest_framework.exceptions import APIException
from claims.models import Claim, ReviewDecision, ClaimDocument
from django.utils.html import format_html
from claims.services import review


class DecisionForm(forms.Form):
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea,
        help_text="Explain this decision in detail. Rejection reasons are visible to the customer.",
    )


class DocumentInline(admin.TabularInline):
    model = ClaimDocument
    extra = 0
    readonly_fields = ("document", "category", "download")

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
    list_display = (
        "id",
        "customer",
        "amount",
        "status",
        "created_at",
        "review_actions",
    )
    list_filter = ("status",)
    readonly_fields = tuple(f.name for f in Claim._meta.fields)
    actions = None
    change_form_template = "admin/claims/claim/change_form.html"
    inlines = [DocumentInline]

    def has_add_permission(self, r):
        return False

    def has_delete_permission(self, r, obj=None):
        return False

    @admin.display(description="Decision")
    def review_actions(self, obj):
        if obj.status not in {Claim.Status.SUBMITTED, Claim.Status.REVIEW}:
            return "Finalized"
        url = reverse("admin:claims_claim_decision", args=[obj.pk])
        return format_html(
            '<a href="{}">Approve</a> / <a href="{}">Reject</a>', url, url
        )

    def get_urls(self):
        return [
            path(
                "<uuid:claim_id>/decision/",
                self.admin_site.admin_view(self.decision_view),
                name="claims_claim_decision",
            )
        ] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        context["decision_url"] = reverse(
            "admin:claims_claim_decision", args=[object_id]
        )
        return super().change_view(request, object_id, form_url, context)

    def decision_view(self, request, claim_id):
        claim = get_object_or_404(Claim, pk=claim_id)
        if not self.has_change_permission(request, claim):
            raise PermissionDenied
        form = DecisionForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                review(
                    claim.pk,
                    request.user,
                    request.POST.get("decision"),
                    form.cleaned_data["reason"],
                )
            except APIException as exc:
                form.add_error(None, str(exc.detail))
            else:
                self.message_user(request, "Claim decision recorded.", messages.SUCCESS)
                return redirect("admin:claims_claim_change", claim.pk)
        return TemplateResponse(
            request,
            "admin/claims/claim/decision.html",
            {
                **self.admin_site.each_context(request),
                "title": "Review claim",
                "opts": self.model._meta,
                "original": claim,
                "form": form,
                "can_decide": claim.status
                in {Claim.Status.SUBMITTED, Claim.Status.REVIEW},
            },
        )


@admin.register(ReviewDecision)
class ReviewAdmin(admin.ModelAdmin):
    readonly_fields = tuple(f.name for f in ReviewDecision._meta.fields)

    def has_add_permission(self, r):
        return False

    def has_change_permission(self, r, obj=None):
        return False

    def has_delete_permission(self, r, obj=None):
        return False
