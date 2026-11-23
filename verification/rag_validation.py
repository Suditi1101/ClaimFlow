"""Semantic checks beyond JSON schema. Reasons never contain document text."""

import json
from verification.rag_clients import RagError

MESSAGES = {
    "missing_evidence_citation": "A supported or concern finding did not cite a claim document. Missing evidence must be labeled missing_information.",
    "missing_policy_citation": "A finding did not cite an applicable policy clause.",
    "unknown_policy_citation": "A finding cited a policy reference outside the retrieved evidence.",
    "unknown_evidence_citation": "A finding cited a claim document outside the supplied evidence.",
    "invalid_json": "The model response could not be parsed as JSON.",
    "invalid_structure": "The model response did not match the required finding fields.",
    "invalid_summary": "The model summary was empty or exceeded 1,000 characters.",
    "invalid_finding_count": "The model must return between one and eight findings.",
    "invalid_finding": "A finding had an invalid outcome or an empty or oversized explanation.",
    "invalid_citation_list": "A citation list was malformed or exceeded six references.",
    "refusal": "The model declined to analyze the supplied information.",
    "truncated": "The model response was cut off before completion.",
}


def invalid(reason):
    raise RagError("invalid_model_findings", detail=reason)


def validate_response(response, evidence, sources):
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            invalid("refusal")
        if choice["finish_reason"] != "stop":
            invalid("truncated")
        try:
            result = json.loads(message["content"])
        except (ValueError, TypeError):
            invalid("invalid_json")
        if not isinstance(result, dict) or set(result) != {"summary", "findings"}:
            invalid("invalid_structure")
        if (
            not isinstance(result["summary"], str)
            or not 1 <= len(result["summary"].strip()) <= 1000
        ):
            invalid("invalid_summary")
        if (
            not isinstance(result["findings"], list)
            or not 1 <= len(result["findings"]) <= 8
        ):
            invalid("invalid_finding_count")
        policy_ids = {source["id"] for source in sources}
        evidence_ids = {page["id"] for page in evidence}
        for finding in result["findings"]:
            if not isinstance(finding, dict) or set(finding) != {
                "outcome",
                "explanation",
                "policy_ids",
                "evidence_ids",
            }:
                invalid("invalid_structure")
            if (
                not isinstance(finding["outcome"], str)
                or finding["outcome"]
                not in {"supported", "concern", "missing_information"}
                or not isinstance(finding["explanation"], str)
                or not 1 <= len(finding["explanation"].strip()) <= 2000
            ):
                invalid("invalid_finding")
            for key, allowed, error in [
                ("policy_ids", policy_ids, "unknown_policy_citation"),
                ("evidence_ids", evidence_ids, "unknown_evidence_citation"),
            ]:
                if (
                    not isinstance(finding[key], list)
                    or len(finding[key]) > 6
                    or any(not isinstance(value, str) for value in finding[key])
                ):
                    invalid("invalid_citation_list")
                if any(value not in allowed for value in finding[key]):
                    invalid(error)
            if not finding["policy_ids"]:
                invalid("missing_policy_citation")
            if (
                finding["outcome"] != "missing_information"
                and not finding["evidence_ids"]
            ):
                invalid("missing_evidence_citation")
        return result
    except (KeyError, IndexError, TypeError, AttributeError):
        invalid("invalid_structure")
