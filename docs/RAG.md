# ClaimFlow RAG verification v1

This is an opt-in, staff-facing verification pipeline. It retrieves policy wording and produces cited advisory findings. It never makes an approval/rejection decision. Existing deterministic policy checks and staff decision controls remain in force. The customer claims API reports assessment status only; raw medical text and unreviewed model conclusions remain in the staff portal. A conversational chat interface is a separate next step.

## Components

- PostgreSQL: immutable policy versions, authoritative policy chunks, and one assessment snapshot per claim.
- Qdrant: dense-vector search of approved policy wording. No customer medical records are indexed into Qdrant in v1.
- Gemini gemini-embedding-001 creates 1,536-dimensional normalized vectors; Groq openai/gpt-oss-120b generates strict JSON-schema findings. Claim evidence is sent to Groq and the retrieval query (which can contain evidence excerpts) to Gemini only after RAG is enabled. Policy wording is sent to Gemini when explicitly indexed. Each provider uses its own API key.
- Existing document Kafka worker: text-PDF extraction, retrieval, generation, and persistence. No new consumer group or topic is required.
- Existing workflow worker: document outcomes remain manual review. Existing policy rules may still reject claims independently of RAG.

## Configure and run with Docker Compose

Set these entries in your existing .env (never commit credentials):

    RAG_ENABLED=true
    GROQ_API_KEY=<your-groq-api-key>
    GEMINI_API_KEY=<your-gemini-api-key>
    RAG_CHAT_MODEL=openai/gpt-oss-120b
    RAG_EMBEDDING_MODEL=gemini-embedding-001
    QDRANT_URL=http://qdrant:6333
    QDRANT_API_KEY=
    RAG_COLLECTION=claimflow_gemini_policy_v1

Rebuild all Python services so workers and web use the same code and migration:

    docker compose build
    docker compose run --rm migrate
    docker compose up -d

Qdrant has persistent storage and no public host port by default. Hosted Qdrant uses an HTTPS QDRANT_URL and QDRANT_API_KEY. Do not expose the local unauthenticated service outside the Compose network. RAG defaults to disabled and no real credentials are included.

## Upload policy documents in Admin

Open Admin > Policies > select a saved policy > Policy Documents > Upload and Index policy PDF. Enter the title, immutable version, effective-from and effective-to dates, then choose the PDF. Save a new policy before uploading documents.

The upload is stored privately and queued. The policy-indexer Compose service processes it in the background. Refresh the policy page to see Pending, Processing, Ready or Failed. The page shows actionable failure details, authorized PDF download, and a Retry indexing confirmation for failed jobs. GET requests never retry a job; retry requires a CSRF-protected POST. Staff need policy change permission for upload, download and retry.

Start the new service after rebuilding and migrating:

    docker compose build
    docker compose run --rm migrate
    docker compose up -d
    docker compose logs --tail=100 policy-indexer

For a one-job development run:

    docker compose exec web python manage.py index_policy_documents --once

Policy indexing requires GEMINI_API_KEY and Qdrant; Groq credentials are required for claim generation, not policy indexing. RAG_ENABLED controls claim processing; explicitly uploading approved policy wording can index it even while claim RAG is disabled. Provider calls never run in the upload HTTP request. Uploaded files and metadata cannot be overwritten; upload corrected PDFs with a new version. Duplicate versions and overlapping active document periods are rejected. A combined policy wording PDF is supported per version; separate overlapping endorsements are not merged in v1.

Jobs interrupted by a worker crash can be reclaimed after ten minutes. Lease tokens prevent an older attempt overwriting a newer attempt's job status. Stable ingestion IDs let failed jobs resume indexing safely. Unexpected indexing errors are shown as a generic safe code; database outages leave the lease recoverable instead of declaring success.

## Ingest operator-approved policy wording from the command line

Use an existing customer's policy reference from Django Admin. The PDF must contain trusted insurer policy wording, not a customer's claim evidence. Version dates determine which wording applies to the incident date. Exactly one ready version must cover that date; overlapping ready versions require operator correction before retrieval can proceed.

Copy a PDF into the running web container:

    docker compose cp ./policy-wording.pdf web:/tmp/policy-wording.pdf

Index it (replace the policy reference and effective dates with the real values):

    docker compose exec web python manage.py ingest_policy_knowledge --policy-reference POL-EXAMPLE --pdf /tmp/policy-wording.pdf --version v1 --title "Hospital policy wording" --effective-from 2026-01-01 --effective-to 2026-12-31

The command extracts pages, stores chunks and their document checksum, embeds them, and upserts stable chunk UUIDs into Qdrant. A version becomes ready only after all upserts finish. Repeating the same ingestion resumes a failed index and is idempotent. A version cannot be reused with different source contents or dates. Identical source content can be deliberately re-embedded into a different collection when the embedding model changes. Published metadata/chunks are read-only in Admin; use a new version for changes. Disable an incorrect ready version through a controlled database/operator procedure; do not create overlapping published date ranges.

Chunks contain up to 1,200 characters with 200-character overlap, without crossing page boundaries. This is character-based chunking, not token-based. Embedding vectors use 1,536 dimensions. Gemini RETRIEVAL_DOCUMENT is used for policy chunks and RETRIEVAL_QUERY for claim searches. Returned vectors are validated and L2-normalized. Do not mix embedding spaces: use a new collection and deliberately re-index applicable knowledge versions.

## Verify claims

New ClaimSubmitted events are handled by the existing document worker when RAG is enabled. Existing claims can be assessed explicitly:

    docker compose exec web python manage.py verify_claim_rag <claim-uuid>

This command accepts pending claims only and does not change a final decision. Existing assessments are reused; repeated Kafka deliveries do not create duplicate assessments or completion events. A new event may race with an existing one and duplicate provider work, but only one assessment is persisted. V1 does not automatically rerun completed or failed assessments; a failed result stays available for staff review. Operational rerun/version history is a later extension.

Go to Admin > Claims > select a claim. The RAG section shows summary, findings, policy references, source pages, extracted evidence, and a safe manual-review code when needed. Staff must inspect findings before using Approve or Reject and recording the actual reason.

## Failure behavior and limits

- Text PDFs only. Image-only pages, encrypted documents, invalid PDFs, and very short unreadable pages require manual extraction/OCR; v1 does not perform OCR.
- Extraction runs in a subprocess with a 15-second timeout, at most 30 pages and 60,000 extracted characters per PDF, and a 10 MiB input limit. On Linux the extraction process also has a 384 MiB address-space limit; Windows relies on process isolation/timeout rather than that Linux limit. Use container memory limits for additional host protection.
- A claim assessment accepts at most ten documents, 30 evidence pages and 30,000 evidence characters total. Exceeding limits requests manual review; evidence is never silently truncated and marked sufficient.
- Storage failures, checksum mismatch, missing/ambiguous policy knowledge, index mismatch, network/provider errors, refusal, malformed output, or unsupported citations produce needs_review, never a successful verification.
- Six dense-vector policy chunks are retrieved with policy/version filters. Returned IDs are rechecked against PostgreSQL ownership/version; Qdrant payload text is not trusted. V1 does not implement hybrid search, reranking, OCR, authenticity verification, fraud detection, or calibrated retrieval thresholds.
- The model has no tools. Uploaded text is untrusted data and cannot change system instructions. Citation membership/schema checks do not prove the model's interpretation is correct; staff review and evaluation are still required.
- Connect/read HTTP timeouts are 5/30 seconds, redirects are disabled, and upstream response bodies are not exposed to customers or logged. Requests are bounded by evidence limits; no automatic provider retry is added.
- Assessments store the retrieved policy text, evidence pages, model, and prompt version for audit. Apply your medical-data retention and provider-handling requirements before enabling with real claims.
- Metrics: claimflow_rag_assessments_total by status, and claimflow_rag_assessment_seconds on the existing worker metrics endpoint. Logs contain claim IDs and safe error codes, not document text or API keys.

## Validate

In a development environment with requirements-dev.lock installed:

    python -m pytest tests/test_rag.py tests/test_claimflow.py tests/test_claim_decisions.py
    python manage.py check
    python manage.py makemigrations --check --dry-run

Tests cover admin uploads, downloads, permissions, CSRF, status transitions, retries, storage integrity and lease recovery as well as RAG ingestion. Tests use generated PDFs and mocked model/vector transports; they do not send real medical records or spend API credits. A live provider/Qdrant smoke test requires your configured services and a synthetic policy/claim. Before production, evaluate correct/wrong policy versions, missing exclusions, missing documents, conflicting evidence, citation support, and model/provider failure on a labeled synthetic set.

## Migrating existing OpenAI indexes to Groq + Gemini

1. Set GROQ_API_KEY, GEMINI_API_KEY and the two model variables shown above. Set RAG_COLLECTION=claimflow_gemini_policy_v1 (or another NEW collection); do not point Gemini at your existing OpenAI index.
2. Rebuild and recreate web, document and policy-indexer services so all use the same settings.
3. Under Admin > Policies > select the policy > Policy Documents, use Re-index with current model for an existing Ready upload, or Retry indexing for a failed upload. Wait for Ready. Previously ingested CLI-only documents can be re-indexed by repeating ingest_policy_knowledge with the original PDF, version and effective dates.
4. Source checksums, dates and chunk IDs remain unchanged. Index metadata switches only after all new vectors are written successfully. Old vector collections are left intact. A model change targeting the same collection is rejected.
5. Historical claim assessments and final decisions are not rewritten by re-indexing. Existing failed assessments are still handled through staff review in v1; new claims use the new index.

This provider integration uses REST through the existing requests dependency; no Groq or Gemini SDK installation is required. Live provider smoke tests are separate from the mocked automated tests.
