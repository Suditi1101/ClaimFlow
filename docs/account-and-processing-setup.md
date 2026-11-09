# Account access and processing setup

The customer portal is /accounts/ with signup at /accounts/signup/ and login at /accounts/login/. Customers remain non-staff and can see only their claims and assigned policies. Claims can be submitted after staff assigns a policy. This does not provide email verification or password-reset email delivery.

The first owner uses /accounts/setup/ with a short-lived one-time code. Its SHA-256 hash and expiration are stored in Vercel as CLAIMFLOW_OWNER_SETUP_HASH and CLAIMFLOW_OWNER_SETUP_EXPIRES. The original code is delivered only to the owner. The link puts it in a URL fragment, which is not sent in server requests; the page removes the fragment and submits the code with CSRF protection. Once setup completes, the durable database flag prevents replay. New signup never grants staff status. Do not publish the setup code or use a shared default password.

Owner setup creates a Django superuser. Normal staff reviewers should instead receive the minimum claims permissions they need. Owner can create customer policies in Django Admin. No sample policy is automatically granted to public registrations.

The existing Supabase project is used for a private claimflow-documents bucket. Django authenticates storage operations with the server-only service-role credential and downloads remain behind the existing ownership check. Production builds create the bucket when absent and verify a synthetic PDF round trip, then delete it. They refuse an existing public bucket. Vercel uploads are limited to 4 MiB to fit request limits. Magic-byte PDF validation is not malware scanning; use synthetic data until a quarantine/scanning integration is configured.

Kafka is still an external dependency. Set KAFKA_BOOTSTRAP_SERVERS, KAFKA_SECURITY_PROTOCOL=SASL_SSL, KAFKA_SASL_MECHANISM=PLAIN (or the broker-supported mechanism), KAFKA_SASL_USERNAME, and KAFKA_SASL_PASSWORD in the Vercel production environment. Store credentials as secrets, never in source or chat.

After the broker is configured, create the topics with manage.py create_topics using the replication factor supported by the broker. Merge ops/vercel-workers.example.json into a real vercel.json and deploy only after verifying the account's Daemons availability and expected resource usage. It declares independent processes for the publisher, three checks, workflow, payments and notifications. This example is intentionally not activated without a broker. No queue simulation or synchronous claim approval is enabled on the live site.

The dashboard explicitly states that automated processing is pending. Database/cache health alone does not establish Kafka/worker readiness. Payment settlement uses a demo provider ledger and notifications are in-app records. Do not represent this deployment as real insurance adjudication or money transfer.

Checks: customer signup privilege boundaries, CSRF, login/logout, throttle, one-time and expired owner setup, existing-owner refusal, customer claim ownership, submission idempotency, storage privacy configuration and adapter failures. Live storage is verified separately during the production build.
