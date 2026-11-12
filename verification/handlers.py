from verification.models import CheckResult
from claims.models import Claim
from common.events import emit


def run_check(claim, kind):
    if kind == "policy":
        p = claim.policy
        ok = (
            p.active
            and p.starts_on <= claim.incident_date <= p.ends_on
            and claim.amount <= p.coverage_limit
        )
        outcome = "pass" if ok else "fail"
        failures = []
        if not p.active:
            failures.append("Your policy is inactive.")
        if not p.starts_on <= claim.incident_date <= p.ends_on:
            failures.append(
                f"The incident date is outside your policy coverage period ({p.starts_on} to {p.ends_on})."
            )
        if claim.amount > p.coverage_limit:
            failures.append(
                f"The claimed amount ({claim.amount}) exceeds your policy coverage limit ({p.coverage_limit})."
            )
        reason = " ".join(failures) if failures else "Policy coverage check passed."
    elif kind == "document":
        outcome = "pass" if claim.document_links.exists() else "review"
        reason = "Demo document presence check"
    else:
        outcome = "review"
        reason = "Fraud assessment requires manual review"
    _, created = CheckResult.objects.get_or_create(
        claim=claim, kind=kind, defaults={"outcome": outcome, "reason": reason}
    )
    if created:
        emit(claim, "CheckCompleted", {"kind": kind})


def advance(claim):
    if claim.status != Claim.Status.SUBMITTED:
        return
    checks = list(claim.checks.all())
    if {c.kind for c in checks} != {"document", "policy", "fraud"}:
        return
    outcomes = {c.outcome for c in checks}
    if "fail" in outcomes:
        status, kind = "rejected", "ClaimRejected"
    elif "review" in outcomes:
        status, kind = "manual_review", "ClaimReviewRequired"
    else:
        status, kind = "approved", "ClaimApproved"
    claim.status = status
    claim.save(update_fields=["status", "updated_at"])
    emit(claim, kind)
