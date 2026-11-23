"""Safe diagnostics and bounded repair for customer chat output."""

import json
import logging
import re
from verification.rag_clients import RagError

log = logging.getLogger(__name__)
CITATION_GROUP = re.compile(r"\[([PCD][0-9]+(?:\s*[,;]\s*[PCD][0-9]+)*)\]")
CHAT_VALIDATION_REASONS = {
    "invalid_structure": "The AI response did not match the expected answer format.",
    "invalid_json": "The AI response could not be read as a structured answer.",
    "invalid_answer": "The AI answer was empty or exceeded the answer length limit.",
    "missing_citations": "The answer did not include supporting source references.",
    "too_many_citations": "The answer included too many source references.",
    "unknown_citation": "The answer referenced a source outside the current conversation evidence.",
    "inline_citation_mismatch": "The answer's inline references did not match its listed sources.",
    "refusal": "The AI service declined to answer this question.",
    "truncated": "The AI answer was cut off before it finished. Try a shorter, more specific question.",
}


def invalid_answer(reason):
    raise RagError("invalid_chat_answer", detail=reason)


def validate_chat_response(response, ids):
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            invalid_answer("refusal")
        if choice["finish_reason"] != "stop":
            invalid_answer("truncated")
        try:
            result = json.loads(message["content"])
        except (ValueError, TypeError):
            invalid_answer("invalid_json")
        if not isinstance(result, dict) or set(result) != {"answer", "source_ids"}:
            invalid_answer("invalid_structure")
        if (
            not isinstance(result["answer"], str)
            or not 1 <= len(result["answer"].strip()) <= 6000
        ):
            invalid_answer("invalid_answer")
        cited = result["source_ids"]
        if not isinstance(cited, list):
            invalid_answer("invalid_structure")
        if not cited:
            invalid_answer("missing_citations")
        if len(cited) > 8:
            invalid_answer("too_many_citations")
        if any(not isinstance(value, str) or value not in ids for value in cited):
            invalid_answer("unknown_citation")
        inline = [
            value
            for group in CITATION_GROUP.findall(result["answer"])
            for value in re.split(r"\s*[,;]\s*", group)
        ]
        if any(value not in cited for value in inline):
            invalid_answer("inline_citation_mismatch")
        return result
    except (KeyError, IndexError, TypeError, AttributeError):
        invalid_answer("invalid_structure")


def generate_chat_answer(payload, ids, completion):
    for attempt in range(2):
        response = completion(payload)
        try:
            result = validate_chat_response(response, ids)
        except RagError as exc:
            log.warning(
                "rag_chat_validation_failed attempt=%s reason=%s",
                attempt + 1,
                exc.detail,
            )
            if attempt == 1 or exc.detail in {"refusal", "truncated"}:
                raise
            payload["messages"].append(
                {
                    "role": "user",
                    "content": "Regenerate a complete answer. Validation failed: "
                    + CHAT_VALIDATION_REASONS[exc.detail]
                    + " Use only current supplied sources. Include 1-8 source_ids, and list every source you cite inline. Do not copy citations from conversation history. If evidence is missing, state that limitation rather than inventing facts or citations.",
                }
            )
        else:
            log.info(
                "rag_chat_validation_passed attempt=%s citations=%s",
                attempt + 1,
                len(result["source_ids"]),
            )
            return result
