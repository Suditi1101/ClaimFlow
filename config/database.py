"""PostgreSQL configuration for local Compose and hosted Supabase connections."""

from urllib.parse import parse_qs, unquote, urlsplit
from django.core.exceptions import ImproperlyConfigured


def database_config(env):
    direct = env.get("CLAIMFLOW_DATABASE_DIRECT") == "1"
    url = (
        (env.get("DATABASE_URL_UNPOOLED") or env.get("CLAIM_POSTGRES_URL_NON_POOLING"))
        if direct
        else (env.get("DATABASE_URL") or env.get("CLAIM_POSTGRES_URL"))
    )
    if direct and not url:
        raise ImproperlyConfigured(
            "A direct or session-pooler database URL is required for migrations."
        )
    if not url:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env.get("POSTGRES_DB", "claimflow"),
            "USER": env.get("POSTGRES_USER", "claimflow"),
            "PASSWORD": env.get("POSTGRES_PASSWORD", ""),
            "HOST": env.get("POSTGRES_HOST", "localhost"),
            "PORT": env.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 5},
        }
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path.strip("/")
            or not parsed.username
        ):
            raise ValueError()
        port = parsed.port or 5432
        sslmode = parse_qs(parsed.query).get("sslmode", ["require"])[-1]
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError()
    except ValueError:
        # Never include URLs or credentials in settings errors.
        raise ImproperlyConfigured(
            "Invalid PostgreSQL URL or insecure TLS mode."
        ) from None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path[1:]),
        "USER": unquote(parsed.username),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(port),
        "CONN_MAX_AGE": 0,
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "OPTIONS": {
            "connect_timeout": 5,
            "sslmode": sslmode,
            "prepare_threshold": None,
        },
    }
