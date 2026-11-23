from django.utils import timezone
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction, connection
from rest_framework.test import APIClient
from claims.models import Claim, Document, ReviewDecision
from policies.models import Policy
from common.models import Outbox, ProcessedEvent
from common.events import envelope, validate
from common.processing import process
from claims.services import submit, review, Conflict
from payments.services import settle, demo_provider
from payments.models import Payment, ProviderReceipt
from verification.models import CheckResult
from notifications.models import Notification
from common.kafka import publish_one, send

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user("alice", password="test-password-42")


@pytest.fixture
def other():
    return get_user_model().objects.create_user("bob")


@pytest.fixture
def policy(user):
    return Policy.objects.create(
        reference="POL-001",
        customer=user,
        starts_on=timezone.localdate() - timedelta(days=365),
        ends_on=timezone.localdate() + timedelta(days=365),
        coverage_limit=Decimal("10000"),
    )


@pytest.fixture
def doc(user):
    return Document.objects.create(
        owner=user,
        file=SimpleUploadedFile("a.pdf", b"%PDF-1.4 demo"),
        sha256="a" * 64,
        size=13,
    )


@pytest.fixture
def data(policy, doc):
    return {
        "policy_reference": policy.reference,
        "amount": Decimal("100.00"),
        "incident_date": timezone.localdate(),
        "description": "Hospital expenses after accident",
        "document_ids": [doc.pk],
    }


@pytest.fixture
def claim(user, data):
    return submit(user, data, "req-1")[0]


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def event(claim, event_type, **data):
    return {
        "id": str(uuid.uuid4()),
        "version": 1,
        "type": event_type,
        "claim_id": str(claim.pk),
        "data": data,
    }


def run_checks(claim):
    e = envelope(Outbox.objects.get(kind="ClaimSubmitted", claim_id=claim.pk))
    for role in ["document", "policy", "fraud"]:
        process(role, e)
    for row in list(Outbox.objects.filter(kind="CheckCompleted")):
        process("workflow", envelope(row))


def test_submission_is_atomic(user, data):
    with patch("claims.services.emit", side_effect=RuntimeError("fail")):
        with pytest.raises(RuntimeError):
            submit(user, data, "atomic")
    assert not Claim.objects.exists() and not Outbox.objects.exists()


def test_duplicate_submission(user, data):
    a, created = submit(user, data, "same")
    b, created2 = submit(user, data, "same")
    assert created and not created2 and a.pk == b.pk and Outbox.objects.count() == 1


def test_key_conflict(user, data):
    submit(user, data, "same")
    data["amount"] = Decimal("101")
    with pytest.raises(Conflict):
        submit(user, data, "same")


def test_cross_owner_policy(other, data):
    with pytest.raises(Exception):
        submit(other, data, "x")
    assert not Claim.objects.exists()


def test_cross_owner_document(user, data, doc, other):
    doc.owner = other
    doc.save()
    with pytest.raises(Exception):
        submit(user, data, "x")
    assert not Outbox.objects.exists()


def test_document_cannot_be_reused(user, data, claim):
    with pytest.raises(Exception):
        submit(user, data, "another")
    assert Claim.objects.count() == 1


def test_api_submission(client, data):
    payload = {
        **data,
        "amount": "100.00",
        "incident_date": str(data["incident_date"]),
        "document_ids": [str(data["document_ids"][0])],
    }
    response = client.post(
        "/api/claims", payload, format="json", HTTP_IDEMPOTENCY_KEY="api-1"
    )
    assert response.status_code == 201, response.data
    assert (
        client.post(
            "/api/claims", payload, format="json", HTTP_IDEMPOTENCY_KEY="api-1"
        ).status_code
        == 200
    )


def test_api_requires_key(client, data):
    assert client.post("/api/claims", {}, format="json").status_code == 400


def test_anonymous_denied():
    assert APIClient().get("/api/claims").status_code == 401


def test_cross_owner_read(client, claim, other):
    client.force_authenticate(other)
    assert client.get(f"/api/claims/{claim.pk}").status_code == 404
    assert client.get("/api/claims").data["count"] == 0


def test_negative_and_future_invalid(client, data):
    payload = {
        **data,
        "amount": "-1",
        "incident_date": str(timezone.localdate() + timedelta(days=1)),
        "document_ids": [str(data["document_ids"][0])],
    }
    res = client.post(
        "/api/claims", payload, format="json", HTTP_IDEMPOTENCY_KEY="invalid"
    )
    assert res.status_code == 400 and not Claim.objects.exists()


def test_document_upload_and_access(client, other):
    res = client.post(
        "/api/documents",
        {"file": SimpleUploadedFile("x.pdf", b"%PDF-1.4 example")},
        format="multipart",
    )
    assert res.status_code == 201
    url = f"/api/documents/{res.data['id']}"
    assert client.get(url).status_code == 200
    client.force_authenticate(other)
    assert client.get(url).status_code == 404


def test_invalid_document(client):
    assert (
        client.post(
            "/api/documents",
            {"file": SimpleUploadedFile("x.pdf", b"bad")},
            format="multipart",
        ).status_code
        == 400
    )


def test_duplicate_consumer_event(claim):
    e = event(claim, "ClaimSubmitted")
    process("policy", e)
    process("policy", e)
    assert CheckResult.objects.count() == 1 and ProcessedEvent.objects.count() == 1
    assert Outbox.objects.filter(kind="CheckCompleted").count() == 1


def test_consumer_transaction_rollback(claim):
    with patch("verification.handlers.emit", side_effect=RuntimeError("fail")):
        with pytest.raises(RuntimeError):
            process("policy", event(claim, "ClaimSubmitted"))
    assert not CheckResult.objects.exists() and not ProcessedEvent.objects.exists()


def test_independent_groups_and_manual_review(claim):
    run_checks(claim)
    claim.refresh_from_db()
    assert claim.status == "manual_review" and claim.checks.count() == 3
    assert Outbox.objects.filter(kind="ClaimReviewRequired").count() == 1


def test_policy_failure_rejects(claim):
    claim.policy.active = False
    claim.policy.save()
    run_checks(claim)
    claim.refresh_from_db()
    assert claim.status == "rejected" and not Payment.objects.exists()


def test_out_of_order_check_events(claim):
    process("workflow", event(claim, "CheckCompleted", kind="policy"))
    claim.refresh_from_db()
    assert claim.status == "submitted"
    run_checks(claim)
    claim.refresh_from_db()
    assert claim.status == "manual_review"


def test_reviewer_permission_and_decision(claim, user):
    run_checks(claim)
    with pytest.raises(Exception):
        review(claim.pk, user, "approved", "checked")
    admin = get_user_model().objects.create_superuser(
        "reviewer", password="test-password-42"
    )
    review(claim.pk, admin, "approved", "All evidence checked")
    assert ReviewDecision.objects.count() == 1
    process("payment", envelope(Outbox.objects.get(kind="ClaimApproved")))
    claim.refresh_from_db()
    assert claim.status == "paid"
    assert Payment.objects.get().status == "settled"


def test_duplicate_payment(claim):
    claim.status = "approved"
    claim.save()
    e = event(claim, "ClaimApproved")
    process("payment", e)
    process("payment", e)
    assert ProviderReceipt.objects.count() == 1 and Payment.objects.count() == 1
    assert Outbox.objects.filter(kind="PaymentSettled").count() == 1


def test_payment_timeout_recovery(claim):
    claim.status = "approved"
    claim.save()

    def ambiguous(key, amount):
        demo_provider(key, amount)
        raise TimeoutError("provider charged but response lost")

    with pytest.raises(TimeoutError):
        settle(claim.pk, ambiguous)
    assert Payment.objects.get().status == "pending"
    settle(claim.pk)
    assert (
        ProviderReceipt.objects.count() == 1
        and Payment.objects.get().status == "settled"
    )


def test_payment_for_unapproved_fails(claim):
    with pytest.raises(ValueError):
        settle(claim.pk)
    assert not ProviderReceipt.objects.exists()


def test_notification_duplicate(claim):
    e = event(claim, "ClaimReviewRequired")
    process("notification", e)
    process("notification", e)
    assert Notification.objects.count() == 1


def test_invalid_event():
    for e in [
        {},
        [],
        {"version": 1, "type": "DeleteEverything"},
        {
            "version": 1,
            "type": "ClaimSubmitted",
            "id": "bad",
            "claim_id": "bad",
            "data": {},
        },
    ]:
        with pytest.raises((ValueError, KeyError, TypeError)):
            validate(e)


def test_outbox_failure_retained(claim):
    with patch("common.kafka.send", side_effect=RuntimeError()):
        assert publish_one(object())
    row = Outbox.objects.get()
    assert row.published_at is None and row.attempts == 1


def test_outbox_success_and_duplicate_safety(claim):
    with patch("common.kafka.send") as sender:
        assert publish_one(object())
    assert sender.call_args.args[2] == claim.pk
    assert Outbox.objects.get().published_at is not None
    assert not publish_one(object())


def test_delivery_requires_ack():
    class Fake:
        def produce(self, *a, **kw):
            pass

        def flush(self, *a):
            return 0

    with pytest.raises(RuntimeError):
        send(Fake(), "topic", "key", {})


def test_db_constraint(user, policy):
    with pytest.raises(IntegrityError), transaction.atomic():
        Claim.objects.create(
            customer=user,
            policy=policy,
            amount=-1,
            incident_date=timezone.localdate(),
            description="invalid",
            idempotency_key="x",
            request_hash="x",
        )


@pytest.mark.django_db(transaction=True)
def test_postgres_concurrent_submission(user, data):
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks; CI runs this test on PostgreSQL")
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections

    def task():
        close_old_connections()
        try:
            return submit(get_user_model().objects.get(pk=user.pk), data, "concurrent")[
                0
            ].pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: task(), range(2)))
    assert (
        ids[0] == ids[1] and Claim.objects.count() == 1 and Outbox.objects.count() == 1
    )


class KafkaMessage:
    def __init__(self, raw):
        self.raw = raw

    def value(self):
        return self.raw

    def topic(self):
        return "claimflow.events.v1"

    def partition(self):
        return 0

    def offset(self):
        return 42


def test_offset_committed_after_db(claim):
    import json
    from unittest.mock import Mock
    from common.consumer import handle_message

    e = event(claim, "ClaimSubmitted")
    consumer = Mock()

    def committed(**kwargs):
        assert kwargs["asynchronous"] is False
        assert ProcessedEvent.objects.filter(
            event_id=e["id"], consumer="policy"
        ).exists()

    consumer.commit.side_effect = committed
    handle_message(consumer, Mock(), "policy", KafkaMessage(json.dumps(e).encode()))
    consumer.commit.assert_called_once()


def test_dlq_ack_before_offset_and_replay_guard():
    from unittest.mock import Mock
    from common.consumer import handle_message
    from common.models import DeadLetter

    consumer = Mock()
    with patch("common.consumer.send", side_effect=RuntimeError("no ack")):
        with pytest.raises(RuntimeError):
            handle_message(consumer, Mock(), "policy", KafkaMessage(b"bad-json"))
    consumer.commit.assert_not_called()
    assert DeadLetter.objects.count() == 1
    with patch("common.consumer.send") as sent:
        handle_message(consumer, Mock(), "policy", KafkaMessage(b"bad-json"))
    assert DeadLetter.objects.count() == 1
    assert "raw_base64" not in sent.call_args.args[3]
    consumer.commit.assert_called_once()


def test_database_outage_never_commits(claim):
    import json
    from unittest.mock import Mock
    from django.db import OperationalError
    from common.consumer import handle_message

    consumer = Mock()
    with patch("common.consumer.process", side_effect=OperationalError("offline")):
        with pytest.raises(OperationalError):
            handle_message(
                consumer,
                Mock(),
                "policy",
                KafkaMessage(json.dumps(event(claim, "ClaimSubmitted")).encode()),
            )
    consumer.commit.assert_not_called()


def test_replay_retains_event_id(claim):
    import base64
    import json
    from common.models import DeadLetter
    from django.core.management import call_command
    from django.core.management.base import CommandError

    e = event(claim, "ClaimSubmitted")
    d = DeadLetter.objects.create(
        source_topic="events",
        partition=0,
        offset=1,
        consumer="policy",
        raw_base64=base64.b64encode(json.dumps(e).encode()).decode(),
        error_code="RuntimeError",
    )
    call_command("replay_dead_letter", d.pk)
    assert ProcessedEvent.objects.filter(event_id=e["id"], consumer="policy").exists()
    with pytest.raises(CommandError):
        call_command("replay_dead_letter", d.pk)


def test_all_checks_pass_approves(claim):
    for kind in ["document", "policy", "fraud"]:
        CheckResult.objects.create(
            claim=claim, kind=kind, outcome="pass", reason="Verified fixture"
        )
    e = event(claim, "CheckCompleted", kind="fraud")
    process("workflow", e)
    process("workflow", e)
    claim.refresh_from_db()
    assert (
        claim.status == "approved"
        and Outbox.objects.filter(kind="ClaimApproved").count() == 1
    )


def test_repeated_review_cannot_change_decision(claim):
    run_checks(claim)
    reviewer = get_user_model().objects.create_superuser(
        "staff", password="test-password-42"
    )
    review(claim.pk, reviewer, "rejected", "Evidence insufficient")
    with pytest.raises(Exception):
        review(claim.pk, reviewer, "approved", "retry")
    assert ReviewDecision.objects.count() == 1


def test_readiness_and_liveness(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    with patch("common.health.cache.set", side_effect=ConnectionError()):
        assert client.get("/health/ready").status_code == 503


def test_oversized_upload_stopped_before_storage(client):
    oversized = b"%PDF-" + b"x" * (10 * 1024 * 1024)
    response = client.post(
        "/api/documents",
        {"file": SimpleUploadedFile("big.pdf", oversized)},
        format="multipart",
    )
    assert response.status_code == 400
    assert Document.objects.count() == 0
