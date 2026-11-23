from django.utils import timezone
from datetime import timedelta
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.core.cache import cache
from claims.models import Claim, ReviewDecision
from policies.models import Policy

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_throttle():
    cache.clear()


@pytest.fixture
def account():
    user = get_user_model().objects.create_user("claim-customer")
    policy = Policy.objects.create(
        reference="CLAIM-TEST",
        customer=user,
        starts_on=timezone.localdate() - timedelta(days=1),
        ends_on=timezone.localdate() + timedelta(days=100),
        coverage_limit=1000,
    )
    return user, policy


def payload(policy, key=None):
    return {
        "policy": policy.pk,
        "amount": "100.00",
        "incident_date": timezone.localdate().isoformat(),
        "description": "Hospital claim with supporting evidence",
        "request_key": key or str(uuid.uuid4()),
        "document": SimpleUploadedFile("claim.pdf", b"%PDF-1.4 claim"),
        "supporting_documents": [
            SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 receipt"),
            SimpleUploadedFile("report.pdf", b"%PDF-1.4 report"),
        ],
    }


def test_multiple_uploads_and_retry(account):
    user, policy = account
    client = Client()
    client.force_login(user)
    key = str(uuid.uuid4())
    assert client.post("/accounts/", payload(policy, key)).status_code == 302
    assert client.post("/accounts/", payload(policy, key)).status_code == 302
    claim = Claim.objects.get()
    assert list(
        claim.document_links.order_by("pk").values_list("category", flat=True)
    ) == ["claim", "supporting", "supporting"]
    changed = payload(policy, key)
    changed["supporting_documents"][1] = SimpleUploadedFile(
        "changed.pdf", b"%PDF-changed"
    )
    assert client.post("/accounts/", changed).status_code == 200
    assert Claim.objects.count() == 1


def test_every_supporting_file_validated(account):
    user, policy = account
    client = Client()
    client.force_login(user)
    data = payload(policy)
    data["supporting_documents"][0] = SimpleUploadedFile("invalid.pdf", b"not a pdf")
    assert client.post("/accounts/", data).status_code == 200
    assert not Claim.objects.exists()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_admin_reason_required_and_audited(account, decision):
    user, policy = account
    client = Client()
    client.force_login(user)
    client.post("/accounts/", payload(policy))
    claim = Claim.objects.get()
    url = f"/admin/claims/claim/{claim.pk}/decision/"
    assert (
        client.post(url, {"decision": decision, "reason": "unauthorized"}).status_code
        == 302
    )
    staff = get_user_model().objects.create_user("unprivileged", is_staff=True)
    client.force_login(staff)
    assert (
        client.post(url, {"decision": decision, "reason": "unauthorized"}).status_code
        == 403
    )
    admin = get_user_model().objects.create_superuser("claim-reviewer")
    client.force_login(admin)
    assert b"Approve" in client.get(f"/admin/claims/claim/{claim.pk}/change/").content
    assert client.post(url, {"decision": decision, "reason": "   "}).status_code == 200
    assert not ReviewDecision.objects.exists()
    reason = "The invoice dates fall outside the policy coverage period."
    assert client.post(url, {"decision": decision, "reason": reason}).status_code == 302
    audit = ReviewDecision.objects.get()
    assert audit.reviewer == admin and audit.reason == reason
    claim.refresh_from_db()
    assert claim.status == decision
    assert (
        client.post(url, {"decision": "approved", "reason": "repeat"}).status_code
        == 200
    )
    assert ReviewDecision.objects.count() == 1
    client.force_login(user)
    response = client.get("/accounts/")
    if decision == "rejected":
        assert b"Reason for rejection" in response.content
        assert reason.encode() in response.content
        assert client.get(f"/api/claims/{claim.pk}").json()["rejection_reasons"] == [
            reason
        ]
    other = get_user_model().objects.create_user("another-customer")
    client.force_login(other)
    assert reason.encode() not in client.get("/accounts/").content
    assert client.get(f"/api/claims/{claim.pk}").status_code == 404


def test_admin_decision_requires_csrf(account):
    user, policy = account
    client = Client()
    client.force_login(user)
    client.post("/accounts/", payload(policy))
    claim = Claim.objects.get()
    admin = get_user_model().objects.create_superuser("csrf-reviewer")
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(admin)
    assert (
        protected.post(
            f"/admin/claims/claim/{claim.pk}/decision/",
            {"decision": "rejected", "reason": "Missing receipt"},
        ).status_code
        == 403
    )
    assert not ReviewDecision.objects.exists()


def test_upload_count_and_combined_size(account):
    from common.portal import ClaimForm
    from django.utils.datastructures import MultiValueDict

    user, policy = account
    data = payload(policy)
    files = MultiValueDict(
        {
            "document": [data.pop("document")],
            "supporting_documents": data.pop("supporting_documents"),
        }
    )
    with override_settings(MAX_DOCUMENT_BYTES=30):
        form = ClaimForm(data, files, customer=user)
        assert not form.is_valid()
        assert "combined" in str(form.errors)
    files.setlist(
        "supporting_documents",
        [SimpleUploadedFile("a.pdf", b"%PDF-test") for _ in range(10)],
    )
    assert not ClaimForm(data, files, customer=user).is_valid()


def test_automated_rejection_explains_policy_failures(account):
    from verification.handlers import run_check

    user, policy = account
    client = Client()
    client.force_login(user)
    client.post("/accounts/", payload(policy))
    claim = Claim.objects.get()
    policy.active = False
    policy.coverage_limit = 1
    policy.save()
    run_check(claim, "policy")
    claim.status = "rejected"
    claim.save()
    reasons = " ".join(claim.rejection_reasons)
    assert "inactive" in reasons and "exceeds" in reasons


def test_partial_upload_over_limit_never_submits(account):
    user, policy = account
    client = Client()
    client.force_login(user)
    with override_settings(MAX_DOCUMENT_BYTES=30):
        response = client.post("/accounts/", payload(policy))
    assert response.status_code == 200
    assert not Claim.objects.exists()
