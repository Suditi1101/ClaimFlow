import hashlib
import hmac
import time
import uuid
from decimal import Decimal
from django import forms
from django.conf import settings
from django.contrib.auth import login, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from rest_framework.exceptions import APIException
from claims.api import ClaimInput
from claims.models import Claim, Document
from claims.services import submit
from common.models import OwnerSetup
from common.storage import StorageUnavailable
from policies.models import Policy


def limited(request, scope, limit):
    identity = request.META.get("REMOTE_ADDR", "unknown")
    key = "portal-rate:" + hashlib.sha256((scope + identity).encode()).hexdigest()
    if cache.add(key, 1, 300):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, 300)
        return False


class PortalLoginView(LoginView):
    template_name = "portal/form.html"
    extra_context = {"title": "Log in to ClaimFlow"}

    def post(self, request, *args, **kwargs):
        if limited(request, "login", 20):
            return HttpResponse(
                "Too many attempts. Try again in five minutes.", status=429
            )
        return super().post(request, *args, **kwargs)


@csrf_protect
@require_http_methods(["GET", "POST"])
def signup(request):
    if request.method == "POST" and limited(request, "signup", 10):
        return HttpResponse("Too many attempts. Try again later.", status=429)
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.is_staff = False
        user.is_superuser = False
        user.save()
        login(request, user)
        return redirect("portal")
    return render(
        request,
        "portal/form.html",
        {"form": form, "title": "Create your customer account"},
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def setup_owner(request):
    if (
        not settings.OWNER_SETUP_HASH
        or time.time() > settings.OWNER_SETUP_EXPIRES
        or OwnerSetup.objects.filter(pk=1, completed_at__isnull=False).exists()
    ):
        return HttpResponse(
            "Owner setup is unavailable or has already been completed.", status=410
        )
    form = UserCreationForm(request.POST or None)
    if request.method == "POST":
        if limited(request, "setup", 10):
            return HttpResponse("Too many attempts. Try again later.", status=429)
        supplied = hashlib.sha256(
            request.POST.get("setup_code", "").encode()
        ).hexdigest()
        if not hmac.compare_digest(supplied, settings.OWNER_SETUP_HASH):
            return HttpResponse("Invalid setup code.", status=403)
        if form.is_valid():
            with transaction.atomic():
                OwnerSetup.objects.get_or_create(pk=1)
                state = OwnerSetup.objects.select_for_update().get(pk=1)
                if (
                    state.completed_at is not None
                    or get_user_model().objects.filter(is_superuser=True).exists()
                ):
                    return HttpResponse("Owner account already exists.", status=410)
                user = form.save(commit=False)
                user.is_staff = True
                user.is_superuser = True
                user.save()
                state.completed_at = timezone.now()
                state.save(update_fields=["completed_at"])
            login(request, user)
            return redirect("/admin/")
    return render(
        request,
        "portal/form.html",
        {
            "form": form,
            "title": "Set up your ClaimFlow owner account",
            "owner_setup": True,
        },
    )


def validate_pdf(file):
    if file.size > settings.MAX_DOCUMENT_BYTES or file.read(5) != b"%PDF-":
        raise forms.ValidationError("Upload a PDF within the size limit.")
    file.seek(0)
    return file


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, **kwargs):
        super().__init__(
            widget=MultipleFileInput(attrs={"accept": "application/pdf"}), **kwargs
        )

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > 9:
            raise forms.ValidationError("Upload at most nine supporting documents.")
        return [
            validate_pdf(super(MultipleFileField, self).clean(file, initial))
            for file in files
        ]


class ClaimForm(forms.Form):
    policy = forms.ModelChoiceField(queryset=Policy.objects.none())
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    incident_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    description = forms.CharField(min_length=10, max_length=4000, widget=forms.Textarea)
    document = forms.FileField(
        label="Claim document",
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
        help_text="Required PDF. Total upload limit: 4 MiB on Vercel, 10 MiB locally.",
    )
    supporting_documents = MultipleFileField(
        required=False,
        help_text="Select up to nine supporting PDFs, such as receipts, prescriptions, and reports.",
    )
    request_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["policy"].queryset = Policy.objects.filter(customer=customer)

    def clean_document(self):
        return validate_pdf(self.cleaned_data["document"])

    def clean(self):
        data = super().clean()
        files = ([data["document"]] if data.get("document") else []) + data.get(
            "supporting_documents", []
        )
        if sum(file.size for file in files) > settings.MAX_DOCUMENT_BYTES:
            raise forms.ValidationError(
                "The combined documents exceed the total upload size limit."
            )
        return data

    def clean_incident_date(self):
        value = self.cleaned_data["incident_date"]
        if value > timezone.localdate():
            raise forms.ValidationError("Incident date cannot be in the future.")
        return value


@login_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def dashboard(request):
    form = ClaimForm(
        request.POST or None,
        request.FILES or None,
        customer=request.user,
        initial={"request_key": uuid.uuid4()},
    )
    if getattr(request, "document_upload_limit_exceeded", False):
        form.add_error(
            None, "The combined documents exceed the total upload size limit."
        )
    if request.method == "POST":
        if limited(request, "claim:" + str(request.user.pk), 20):
            return HttpResponse("Too many submissions.", status=429)
        if form.is_valid():
            files = [form.cleaned_data["document"]] + form.cleaned_data[
                "supporting_documents"
            ]
            digests = []
            for file in files:
                digest = hashlib.sha256()
                for chunk in file.chunks():
                    digest.update(chunk)
                file.seek(0)
                digests.append(digest.hexdigest())
            key = str(form.cleaned_data["request_key"])
            saved_names = []
            try:
                with transaction.atomic():
                    get_user_model().objects.select_for_update().get(pk=request.user.pk)
                    existing = Claim.objects.filter(
                        customer=request.user, idempotency_key=key
                    ).first()
                    if existing:
                        links = list(
                            existing.document_links.select_related("document").order_by(
                                "pk"
                            )
                        )
                        if [
                            (link.category, link.document.sha256) for link in links
                        ] != [
                            ("claim" if index == 0 else "supporting", digest)
                            for index, digest in enumerate(digests)
                        ]:
                            raise forms.ValidationError(
                                "This request key was used with different documents."
                            )
                        docs = [link.document_id for link in links]
                    else:
                        docs = []
                        for file, digest in zip(files, digests):
                            file.name = uuid.uuid4().hex + ".pdf"
                            doc = Document.objects.create(
                                owner=request.user,
                                file=file,
                                sha256=digest,
                                size=file.size,
                            )
                            saved_names.append(doc.file.name)
                            docs.append(doc.pk)
                    data = ClaimInput(
                        data={
                            "policy_reference": form.cleaned_data["policy"].reference,
                            "amount": form.cleaned_data["amount"],
                            "incident_date": form.cleaned_data["incident_date"],
                            "description": form.cleaned_data["description"],
                            "document_ids": docs,
                            "supporting_document_ids": docs[1:],
                        }
                    )
                    data.is_valid(raise_exception=True)
                    submit(request.user, data.validated_data, key)
                return redirect("portal")
            except (APIException, forms.ValidationError, StorageUnavailable) as exc:
                from django.core.files.storage import default_storage

                for saved_name in saved_names:
                    try:
                        default_storage.delete(saved_name)
                    except (OSError, StorageUnavailable):
                        pass
                if isinstance(exc, StorageUnavailable):
                    form.add_error(
                        None,
                        "Document storage is temporarily unavailable. Please try again.",
                    )
                else:
                    form.add_error(None, str(getattr(exc, "detail", exc)))
    return render(
        request,
        "portal/dashboard.html",
        {
            "form": form,
            "claims": Claim.objects.filter(customer=request.user)
            .prefetch_related(
                "checks", "reviewdecision_set", "document_links__document"
            )
            .order_by("-created_at")[:50],
            "policies": Policy.objects.filter(customer=request.user),
        },
    )
