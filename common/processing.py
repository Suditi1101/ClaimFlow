from django.db import transaction
from claims.models import Claim
from common.models import ProcessedEvent
from common.events import validate
from verification.handlers import run_check, advance
from payments.services import settle
from notifications.models import Notification

ROLES = {
    "document": {"ClaimSubmitted"},
    "policy": {"ClaimSubmitted"},
    "fraud": {"ClaimSubmitted"},
    "workflow": {"CheckCompleted"},
    "payment": {"ClaimApproved"},
    "notification": {"ClaimReviewRequired", "ClaimRejected", "PaymentSettled"},
}


def process(role, event):
    validate(event)
    if role not in ROLES:
        raise ValueError("invalid_role")
    if event["type"] not in ROLES[role]:
        return
    if role == "payment":
        settle(event["claim_id"])
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=event["claim_id"])
        if ProcessedEvent.objects.filter(event_id=event["id"], consumer=role).exists():
            return
        if role in {"document", "policy", "fraud"}:
            run_check(claim, role)
        elif role == "workflow":
            advance(claim)
        elif role == "notification":
            Notification.objects.get_or_create(claim=claim, kind=event["type"])
        ProcessedEvent.objects.create(event_id=event["id"], consumer=role)
