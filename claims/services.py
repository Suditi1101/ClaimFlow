import hashlib
import json
from django.db import transaction
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError, APIException
from claims.models import Claim, Document, ClaimDocument, ReviewDecision
from policies.models import Policy
from common.events import emit


class Conflict(APIException):
    status_code = 409
    default_detail = "Idempotency key reused with different input."


@transaction.atomic
def submit(customer, data, key):
    get_user_model().objects.select_for_update().get(pk=customer.pk)
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()
    existing = Claim.objects.filter(customer=customer, idempotency_key=key).first()
    if existing:
        if existing.request_hash != digest:
            raise Conflict()
        return existing, False
    policy = Policy.objects.filter(
        reference=data["policy_reference"], customer=customer
    ).first()
    if not policy:
        raise ValidationError({"policy_reference": "Unknown policy."})
    docs = list(
        Document.objects.select_for_update().filter(
            pk__in=data["document_ids"], owner=customer
        )
    )
    if (
        len(docs) != len(data["document_ids"])
        or ClaimDocument.objects.filter(document__in=docs).exists()
    ):
        raise ValidationError(
            {"document_ids": "Documents must be owned and unattached."}
        )
    supporting = data.get("supporting_document_ids", [])
    if len(set(supporting)) != len(supporting) or not set(supporting).issubset(
        data["document_ids"]
    ):
        raise ValidationError("Invalid supporting document IDs.")
    if supporting and len(set(data["document_ids"]) - set(supporting)) != 1:
        raise ValidationError("Provide exactly one claim document.")
    documents_by_id = {doc.pk: doc for doc in docs}
    docs = [documents_by_id[doc_id] for doc_id in data["document_ids"]]
    claim = Claim.objects.create(
        customer=customer,
        policy=policy,
        amount=data["amount"],
        incident_date=data["incident_date"],
        description=data["description"],
        idempotency_key=key,
        request_hash=digest,
    )
    ClaimDocument.objects.bulk_create(
        [
            ClaimDocument(
                claim=claim,
                document=d,
                category="supporting"
                if d.pk in data.get("supporting_document_ids", [])
                else "claim",
            )
            for d in docs
        ]
    )
    emit(claim, "ClaimSubmitted")
    return claim, True


@transaction.atomic
def review(claim_id, reviewer, decision, reason):
    if not reviewer.is_staff or not reviewer.has_perm("claims.change_claim"):
        raise ValidationError("Reviewer permission required.")
    claim = Claim.objects.select_for_update().get(pk=claim_id)
    if claim.status not in {Claim.Status.SUBMITTED, Claim.Status.REVIEW}:
        raise ValidationError("Claim is not awaiting review.")
    if (
        decision not in {"approved", "rejected"}
        or not reason.strip()
        or len(reason.strip()) > 1000
    ):
        raise ValidationError("Decision and reason required.")
    claim.status = decision
    claim.save(update_fields=["status", "updated_at"])
    ReviewDecision.objects.create(
        claim=claim, reviewer=reviewer, decision=decision, reason=reason.strip()
    )
    emit(claim, "ClaimApproved" if decision == "approved" else "ClaimRejected")
    return claim
