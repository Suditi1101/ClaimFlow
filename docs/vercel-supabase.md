# Vercel with Supabase PostgreSQL

Django reads DATABASE_URL or the Supabase integration's CLAIM_POSTGRES_URL. Production builds use DATABASE_URL_UNPOOLED or CLAIM_POSTGRES_URL_NON_POOLING, which must be a direct or session-pooler connection. No connection strings belong in source control.

URL credentials are decoded; TLS defaults to require. URL configurations disable persistent Django connections, server-side cursors and psycopg automatic prepared statements for transaction-pooler compatibility. Provider-specific query parameters are not passed to psycopg.

The Vercel build script applies committed migrations only when VERCEL_ENV=production. Preview builds never migrate the production database. Use separate Supabase projects/branches and environment scopes for isolated previews.

After migrations, secure_supabase_tables enables row-level security without public policies on Django-managed tables and revokes table privileges from Supabase's anon and authenticated roles. Django uses its table-owner database credential and performs application authorization through DRF. This prevents Supabase's separate REST API from bypassing Django ownership rules. The command does not change Supabase Auth or Storage tables. Use dedicated database roles and review grants before collecting real claims.

Health readiness reports database and cache separately without exposing connection details. A connected database with no Redis still returns HTTP 503. Supply REDIS_URL for shared throttling and readiness. Kafka workers and private document storage require their own hosted configuration; database setup does not deploy them or create an administrator account.

Migrations are additive initial-schema operations. Never put TESTING=1 on Vercel. Revisit automatic production migrations before larger schema changes or multiple concurrent deployment pipelines.
