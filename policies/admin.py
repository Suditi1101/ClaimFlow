from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from policies.models import Policy, PolicyDocument
from policies.forms import PolicyDocumentForm
from policies.documents import upload_document


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "coverage_limit", "active")
    readonly_fields = ("reference",)
    change_form_template = "admin/policies/policy/change_form.html"

    def get_fields(self, request, obj=None):
        fields = ["customer", "starts_on", "ends_on", "coverage_limit", "active"]
        return ["reference", *fields] if obj is not None else fields

    def get_urls(self):
        return [
            path(
                "<int:object_id>/documents/upload/",
                self.admin_site.admin_view(self.upload_view),
                name="policies_policy_upload_document",
            ),
            path(
                "<int:object_id>/documents/<uuid:document_id>/download/",
                self.admin_site.admin_view(self.download_view),
                name="policies_policy_document_download",
            ),
            path(
                "<int:object_id>/documents/<uuid:document_id>/retry/",
                self.admin_site.admin_view(self.retry_view),
                name="policies_policy_document_retry",
            ),
        ] + super().get_urls()

    def authorized_policy(self, request, object_id):
        policy = get_object_or_404(self.get_queryset(request), pk=object_id)
        if not self.has_change_permission(request, policy):
            raise PermissionDenied
        return policy

    def upload_view(self, request, object_id):
        policy = self.authorized_policy(request, object_id)
        if request.method not in {"GET", "POST"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        form = PolicyDocumentForm(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
            policy=policy,
            initial={
                "effective_from": policy.starts_on,
                "effective_to": policy.ends_on,
            },
        )
        if getattr(request, "document_upload_limit_exceeded", False):
            form.add_error("file", "The PDF exceeds the upload size limit.")
        if request.method == "POST" and form.is_valid():
            try:
                document = upload_document(policy, request.user, form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, exc)
            except OSError:
                form.add_error(
                    None,
                    "Document storage is temporarily unavailable. Please try again.",
                )
            else:
                self.log_change(
                    request,
                    policy,
                    f"Uploaded policy document {document.pk} for indexing.",
                )
                self.message_user(
                    request,
                    "Policy document uploaded. Indexing is pending; refresh this policy page to see its status.",
                    messages.SUCCESS,
                )
                return redirect("admin:policies_policy_change", policy.pk)
        return TemplateResponse(
            request,
            "admin/policies/policy/document_upload.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "Upload policy document",
                "policy": policy,
                "form": form,
            },
        )

    def download_view(self, request, object_id, document_id):
        policy = self.authorized_policy(request, object_id)
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        document = get_object_or_404(PolicyDocument, policy=policy, pk=document_id)
        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=f"policy-{document.pk}.pdf",
            content_type="application/pdf",
        )

    def retry_view(self, request, object_id, document_id):
        policy = self.authorized_policy(request, object_id)
        document = get_object_or_404(PolicyDocument, policy=policy, pk=document_id)
        if request.method not in {"GET", "POST"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        if request.method == "POST":
            eligible_status = (
                "failed"
                if document.status == "failed"
                else (
                    "ready"
                    if document.status == "ready" and document.needs_reindex
                    else None
                )
            )
            changed = PolicyDocument.objects.filter(
                pk=document.pk, status=eligible_status
            ).update(
                status="pending", error_code="", processing_token=None, started_at=None
            )
            if changed:
                self.log_change(
                    request, policy, f"Retried indexing policy document {document.pk}."
                )
                self.message_user(
                    request,
                    "Policy document queued for indexing again.",
                    messages.SUCCESS,
                )
            else:
                self.message_user(
                    request,
                    "Only failed documents or ready documents with changed embedding configuration can be re-indexed.",
                    messages.ERROR,
                )
            return redirect("admin:policies_policy_change", policy.pk)
        return TemplateResponse(
            request,
            "admin/policies/policy/document_retry.html",
            {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "Retry policy document indexing",
                "policy": policy,
                "document": document,
            },
        )
