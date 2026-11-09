import hashlib
import time
import uuid
from datetime import date, timedelta
from unittest.mock import Mock, patch
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from common.models import OwnerSetup, Outbox
from common.storage import SupabaseStorage, StorageUnavailable
from claims.models import Claim
from policies.models import Policy

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_throttle():
    cache.clear()


def credentials(username="customer"):
    return {
        "username": username,
        "password1": "test-Customer-82!safe",
        "password2": "test-Customer-82!safe",
    }


def test_signup_is_never_staff():
    c = Client()
    res = c.post(
        "/accounts/signup/",
        {**credentials(), "is_staff": "true", "is_superuser": "true"},
    )
    assert res.status_code == 302
    u = get_user_model().objects.get(username="customer")
    assert u.check_password(credentials()["password1"])
    assert not u.is_staff and not u.is_superuser
    assert c.get("/accounts/").status_code == 200
    assert c.get("/admin/").status_code == 302


def test_signup_requires_csrf():
    c = Client(enforce_csrf_checks=True)
    assert c.post("/accounts/signup/", credentials()).status_code == 403


def test_login_logout():
    u = get_user_model().objects.create_user(
        "customer", password=credentials()["password1"]
    )
    c = Client()
    assert (
        c.post(
            "/accounts/login/",
            {"username": u.username, "password": credentials()["password1"]},
        ).status_code
        == 302
    )
    assert c.get("/accounts/").status_code == 200
    assert c.get("/accounts/logout/").status_code == 405
    assert c.post("/accounts/logout/").status_code == 302
    assert c.get("/accounts/").status_code == 302


def test_login_throttled():
    c = Client()
    for _ in range(20):
        c.post("/accounts/login/", {"username": "missing", "password": "bad"})
    assert (
        c.post(
            "/accounts/login/", {"username": "missing", "password": "bad"}
        ).status_code
        == 429
    )


def test_owner_setup_valid_once():
    token = "synthetic-owner-code"
    with override_settings(
        OWNER_SETUP_HASH=hashlib.sha256(token.encode()).hexdigest(),
        OWNER_SETUP_EXPIRES=int(time.time()) + 100,
    ):
        c = Client()
        assert (
            c.post(
                "/accounts/setup/", {**credentials("owner"), "setup_code": "wrong"}
            ).status_code
            == 403
        )
        assert not get_user_model().objects.filter(is_superuser=True).exists()
        assert (
            c.post(
                "/accounts/setup/", {**credentials("owner"), "setup_code": token}
            ).status_code
            == 302
        )
        assert get_user_model().objects.get(username="owner").is_superuser
        assert OwnerSetup.objects.get(pk=1).completed_at is not None
        assert (
            Client()
            .post("/accounts/setup/", {**credentials("attacker"), "setup_code": token})
            .status_code
            == 410
        )


def test_expired_owner_setup():
    with override_settings(OWNER_SETUP_HASH="hash", OWNER_SETUP_EXPIRES=1):
        assert Client().get("/accounts/setup/").status_code == 410


def test_existing_admin_blocks_new_owner():
    get_user_model().objects.create_superuser("existing")
    token = "synthetic"
    with override_settings(
        OWNER_SETUP_HASH=hashlib.sha256(token.encode()).hexdigest(),
        OWNER_SETUP_EXPIRES=int(time.time()) + 100,
    ):
        assert (
            Client()
            .post("/accounts/setup/", {**credentials("owner"), "setup_code": token})
            .status_code
            == 410
        )
    assert not get_user_model().objects.filter(username="owner").exists()


def claim_payload(policy, key):
    return {
        "policy": policy.pk,
        "amount": "100.00",
        "incident_date": date.today().isoformat(),
        "description": "Synthetic hospital claim receipt",
        "request_key": key,
        "document": SimpleUploadedFile("demo.pdf", b"%PDF-1.4 synthetic"),
    }


def test_customer_claim_portal_is_owned_and_idempotent():
    u = get_user_model().objects.create_user("one")
    other = get_user_model().objects.create_user("two")
    p = Policy.objects.create(
        reference="P1",
        customer=u,
        starts_on=date.today() - timedelta(days=1),
        ends_on=date.today() + timedelta(days=100),
        coverage_limit=1000,
    )
    c = Client()
    c.force_login(u)
    key = str(uuid.uuid4())
    assert c.post("/accounts/", claim_payload(p, key)).status_code == 302
    assert c.post("/accounts/", claim_payload(p, key)).status_code == 302
    assert Claim.objects.count() == 1 and Outbox.objects.count() == 1
    c.force_login(other)
    assert b"No claims submitted yet" in c.get("/accounts/").content
    assert c.post("/accounts/", claim_payload(p, str(uuid.uuid4()))).status_code == 200
    assert Claim.objects.count() == 1


@override_settings(
    SUPABASE_STORAGE_URL="https://synthetic.supabase.co",
    SUPABASE_STORAGE_KEY="synthetic-key",
    SUPABASE_STORAGE_BUCKET="private-docs",
)
def test_private_storage_roundtrip_and_timeouts():
    storage = SupabaseStorage()
    response = Mock(status_code=200, content=b"%PDF-check")
    with patch("common.storage.requests.request", return_value=response) as call:
        storage._save("a/b.pdf", ContentFile(b"%PDF-check"))
        assert storage._open("a/b.pdf").read() == b"%PDF-check"
        assert call.call_args.kwargs["timeout"] == (5, 20)
        assert call.call_args.kwargs["allow_redirects"] is False
    with patch.object(
        storage,
        "request",
        return_value=Mock(status_code=200, json=lambda: {"public": True}),
    ):
        with pytest.raises(StorageUnavailable):
            storage.ensure_bucket()
    with pytest.raises(NotImplementedError):
        storage.url("a.pdf")
    for path in ["/a", "../a", "a/../b"]:
        with pytest.raises(StorageUnavailable):
            storage.object_path(path)


@override_settings(
    SUPABASE_STORAGE_URL="http://untrusted.example", SUPABASE_STORAGE_KEY="synthetic"
)
def test_storage_rejects_untrusted_origin():
    with pytest.raises(StorageUnavailable):
        SupabaseStorage()
