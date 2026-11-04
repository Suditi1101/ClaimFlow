# ClaimFlow verification

Executed on Python 3.12 in the build workspace.

| Check | Result |
|---|---|
| `TESTING=1 pytest -q` | 36 passed, 1 skipped in 6.00 seconds |
| `TESTING=1 python manage.py check` | No issues |
| `TESTING=1 python manage.py makemigrations --check --dry-run` | No changes detected |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `pip check` | No broken requirements |
| Compose references and monitoring YAML/JSON parsing | Passed; not a container runtime test |

Tests cover atomic claim/outbox rollback, request idempotency and conflicts, unauthorized and cross-owner access, private PDF uploads/downloads, early upload-size enforcement, input validation, duplicate consumer events, transactional consumer rollback, independent groups, manual review/rejection/approval, out-of-order check completion, duplicate payments, ambiguous provider-timeout recovery, notification deduplication, event validation, broker delivery acknowledgment requirements, outbox retries, offset-after-DB ordering, DLQ-before-offset ordering, DB outage offset preservation, controlled replay, health checks and database constraints.

PostgreSQL concurrency is a separate test and was skipped locally because PostgreSQL is unavailable. The included GitHub Actions workflow runs the suite against disposable PostgreSQL and enables that test; CI has not been executed from this workspace. SQLite passing tests do not establish PostgreSQL row-lock correctness.

Docker is unavailable in this environment. The full Compose stack, Kafka broker integration, S3-compatible storage integration, Redis integration, container image pulls, live OTLP export, Prometheus scraping and Grafana provisioning have not been runtime verified. Kafka delivery and failure boundaries use test doubles. No load test, production deployment, backup restore drill or dependency vulnerability scan was performed.

The host's preinstalled anyio pytest plugin emitted one pytest deprecation warning; application tests passed. Resolved runtime and development dependencies are recorded in lock files.

## Self-review

This was a self-review, not independent agent verification. Reviewed authentication and ownership, transaction scope, claim transitions, outbox/inbox uniqueness, external payment failure windows, retry/offset ordering, private document handling, admin write restrictions, telemetry and local deployment boundaries. Fixed upload spooling limits and tightened UUID routes during review. Payment provider side effects remain outside local transaction scope and use stable provider keys.

Known extension requirements are documented in README: malware/content assessment, real fraud assessment, real payment provider reconciliation, external notification delivery, richer review reasons, retained tracing, Kafka lag/outbox-age metrics and production hardening. The supplied provider ledger never transfers real funds. No compliance or production-readiness guarantee is made.
