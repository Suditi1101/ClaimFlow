from datetime import date, timedelta
import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from policies.admin import PolicyAdmin
from policies.models import Policy


@pytest.mark.django_db
def test_policy_reference_generated_and_stable():
    customer = get_user_model().objects.create_user("policy-customer")
    data = dict(
        customer=customer,
        starts_on=date.today(),
        ends_on=date.today() + timedelta(days=365),
        coverage_limit=1000,
    )
    first = Policy.objects.create(**data)
    second = Policy.objects.create(**data)
    assert first.reference.startswith("POL-")
    assert len(first.reference) == 36
    assert first.reference != second.reference
    reference = first.reference
    first.active = False
    first.save()
    first.refresh_from_db()
    assert first.reference == reference
    existing = Policy.objects.create(reference="LEGACY-POLICY-001", **data)
    existing.save()
    existing.refresh_from_db()
    assert existing.reference == "LEGACY-POLICY-001"


def test_admin_does_not_request_policy_reference():
    policy_admin = PolicyAdmin(Policy, AdminSite())
    request = RequestFactory().get("/admin/policies/policy/add/")
    assert "reference" not in policy_admin.get_fields(request)
    assert "reference" in policy_admin.get_readonly_fields(request)
    assert Policy._meta.get_field("reference").editable is False
