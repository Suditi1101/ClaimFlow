import uuid
from django.db import transaction
from payments.models import Payment, ProviderReceipt
from claims.models import Claim
from common.events import emit


def demo_provider(key, amount):
    receipt, _ = ProviderReceipt.objects.get_or_create(
        key=key, defaults={"amount": amount, "reference": f"demo-{uuid.uuid4()}"}
    )
    if receipt.amount != amount:
        raise ValueError("provider_amount_mismatch")
    return receipt.reference


def settle(claim_id, provider=demo_provider):
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.status not in {"approved", "paid"}:
            raise ValueError("claim_not_approved")
        payment, _ = Payment.objects.get_or_create(
            claim=claim,
            defaults={"amount": claim.amount, "idempotency_key": f"claim:{claim.pk}"},
        )
        if payment.status == "settled":
            return
        key, amount = payment.idempotency_key, payment.amount
    reference = provider(key, amount)
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        payment = Payment.objects.select_for_update().get(claim=claim)
        if payment.status == "settled":
            return
        payment.status = "settled"
        payment.provider_reference = reference
        payment.save()
        claim.status = "paid"
        claim.save(update_fields=["status", "updated_at"])
        emit(claim, "PaymentSettled")
