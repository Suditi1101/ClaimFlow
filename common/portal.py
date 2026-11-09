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


class ClaimForm(forms.Form):
    policy = forms.ModelChoiceField(queryset=Policy.objects.none())
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    incident_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    description = forms.CharField(min_length=10, max_length=4000, widget=forms.Textarea)
    document = forms.FileField(help_text="PDF only, maximum 4 MiB on Vercel.")
    request_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["policy"].queryset = Policy.objects.filter(customer=customer)

    def clean_document(self):
        file = self.cleaned_data["document"]
        if file.size > settings.MAX_DOCUMENT_BYTES or file.read(5) != b"%PDF-":
            raise forms.ValidationError("Upload a PDF within the size limit.")
        file.seek(0)
        return file

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
    if request.method == "POST":
        if limited(request, "claim:" + str(request.user.pk), 20):
            return HttpResponse("Too many submissions.", status=429)
        if form.is_valid():
            file = form.cleaned_data["document"]
            digest = hashlib.sha256()
            for chunk in file.chunks():
                digest.update(chunk)
            file.seek(0)
            key = str(form.cleaned_data["request_key"])
            saved_name = None
            try:
                with transaction.atomic():
                    get_user_model().objects.select_for_update().get(pk=request.user.pk)
                    existing = Claim.objects.filter(
                        customer=request.user, idempotency_key=key
                    ).first()
                    if existing:
                        docs = list(
                            existing.document_links.values_list(
                                "document_id", flat=True
                            )
                        )
                        link = existing.document_links.select_related(
                            "document"
                        ).first()
                        if not link or link.document.sha256 != digest.hexdigest():
                            raise forms.ValidationError(
                                "This request key was used with a different document."
                            )
                    else:
                        file.name = uuid.uuid4().hex + ".pdf"
                        doc = Document.objects.create(
                            owner=request.user,
                            file=file,
                            sha256=digest.hexdigest(),
                            size=file.size,
                        )
                        saved_name = doc.file.name
                        docs = [doc.pk]
                    data = ClaimInput(
                        data={
                            "policy_reference": form.cleaned_data["policy"].reference,
                            "amount": form.cleaned_data["amount"],
                            "incident_date": form.cleaned_data["incident_date"],
                            "description": form.cleaned_data["description"],
                            "document_ids": docs,
                        }
                    )
                    data.is_valid(raise_exception=True)
                    submit(request.user, data.validated_data, key)
                return redirect("portal")
            except (APIException, forms.ValidationError) as exc:
                if saved_name:
                    from django.core.files.storage import default_storage

                    try:
                        default_storage.delete(saved_name)
                    except OSError:
                        pass
                form.add_error(None, str(getattr(exc, "detail", exc)))
            except StorageUnavailable:
                form.add_error(
                    None,
                    "Document storage is temporarily unavailable. Please try again.",
                )
    return render(
        request,
        "portal/dashboard.html",
        {
            "form": form,
            "claims": Claim.objects.filter(customer=request.user)
            .prefetch_related("checks")
            .order_by("-created_at")[:50],
            "policies": Policy.objects.filter(customer=request.user),
        },
    )
