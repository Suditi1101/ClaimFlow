import uuid
from opentelemetry.propagate import inject
from common.models import Outbox

KINDS = {
    "ClaimSubmitted",
    "CheckCompleted",
    "ClaimApproved",
    "ClaimRejected",
    "ClaimReviewRequired",
    "PaymentSettled",
}


def emit(claim, kind, payload=None):
    carrier = {}
    inject(carrier)
    return Outbox.objects.create(
        claim_id=claim.pk,
        kind=kind,
        payload=payload or {},
        traceparent=carrier.get("traceparent", ""),
    )


def envelope(row):
    return {
        "id": str(row.pk),
        "version": 1,
        "type": row.kind,
        "claim_id": str(row.claim_id),
        "occurred_at": row.created_at.isoformat(),
        "data": row.payload,
        "traceparent": row.traceparent,
    }


def validate(e):
    if not isinstance(e, dict) or e.get("version") != 1 or e.get("type") not in KINDS:
        raise ValueError("invalid_envelope")
    uuid.UUID(e["id"])
    uuid.UUID(e["claim_id"])
    if not isinstance(e.get("data"), dict):
        raise ValueError("invalid_data")
    if e["type"] == "CheckCompleted" and e["data"].get("kind") not in {
        "document",
        "policy",
        "fraud",
    }:
        raise ValueError("invalid_check")
    if (
        not isinstance(e.get("traceparent", ""), str)
        or len(e.get("traceparent", "")) > 255
    ):
        raise ValueError("invalid_trace")
    return e
