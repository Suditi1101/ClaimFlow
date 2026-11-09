"""Apply committed migrations only on a production deployment."""

import os
import subprocess
import sys


def main():
    if os.getenv("VERCEL_ENV") != "production":
        print("Skipping database migrations outside production.")
        return
    env = {**os.environ, "CLAIMFLOW_DATABASE_DIRECT": "1"}
    subprocess.run(
        [sys.executable, "manage.py", "migrate", "--noinput"], env=env, check=True
    )

    if os.getenv("CLAIM_POSTGRES_URL"):
        subprocess.run(
            [sys.executable, "manage.py", "secure_supabase_tables"], env=env, check=True
        )


if __name__ == "__main__":
    main()
