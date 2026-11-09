import pytest
from django.core.exceptions import ImproperlyConfigured
from config.database import database_config


def test_supabase_pooler_url():
    result = database_config(
        {
            "CLAIM_POSTGRES_URL": "postgres://postgres.project:p%40ss@pooler.example:6543/postgres?sslmode=require&supa=base-pooler.x"
        }
    )
    assert result["PASSWORD"] == "p@ss"
    assert result["PORT"] == "6543"
    assert result["CONN_MAX_AGE"] == 0
    assert result["DISABLE_SERVER_SIDE_CURSORS"]
    assert result["OPTIONS"] == {
        "connect_timeout": 5,
        "sslmode": "require",
        "prepare_threshold": None,
    }


def test_migrations_select_session_pooler():
    result = database_config(
        {
            "CLAIMFLOW_DATABASE_DIRECT": "1",
            "CLAIM_POSTGRES_URL": "postgres://u:p@tx.example:6543/db",
            "CLAIM_POSTGRES_URL_NON_POOLING": "postgres://u:p@session.example:5432/db",
        }
    )
    assert result["HOST"] == "session.example" and result["PORT"] == "5432"


def test_missing_direct_url_fails_closed():
    with pytest.raises(ImproperlyConfigured):
        database_config({"CLAIMFLOW_DATABASE_DIRECT": "1"})


@pytest.mark.parametrize(
    "url",
    [
        "mysql://u:p@example/db",
        "postgres://u:secret@example/db?sslmode=disable",
        "not-a-url",
    ],
)
def test_invalid_url_redacts_credentials(url):
    with pytest.raises(ImproperlyConfigured) as err:
        database_config({"DATABASE_URL": url})
    assert url not in str(err.value) and "secret" not in str(err.value)


def test_compose_settings_preserved():
    result = database_config({"POSTGRES_HOST": "postgres", "POSTGRES_DB": "local"})
    assert result["HOST"] == "postgres" and result["NAME"] == "local"


def test_preview_build_never_migrates(monkeypatch):
    from unittest.mock import patch
    import build

    monkeypatch.setenv("VERCEL_ENV", "preview")
    with patch("build.subprocess.run") as run:
        build.main()
    run.assert_not_called()


def test_production_build_migrates_then_restricts_supabase(monkeypatch):
    from unittest.mock import patch
    import build

    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("CLAIM_POSTGRES_URL", "postgres://fixture")
    with patch("build.subprocess.run") as run:
        build.main()
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][2] == "migrate"
    assert run.call_args_list[1].args[0][2] == "secure_supabase_tables"
    assert run.call_args_list[0].kwargs["env"]["CLAIMFLOW_DATABASE_DIRECT"] == "1"


@pytest.mark.django_db(transaction=True)
def test_supabase_tables_have_row_security():
    from django.db import connection
    from django.core.management import call_command

    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row security")
    try:
        call_command("secure_supabase_tables")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = 'claims_claim'::regclass"
            )
            assert cursor.fetchone()[0] is True
    finally:
        # Restore the isolated test database policy state for following tests.
        from django.apps import apps

        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            for model in apps.get_models(include_auto_created=True):
                if model._meta.managed and model._meta.db_table in tables:
                    cursor.execute(
                        f"ALTER TABLE {connection.ops.quote_name(model._meta.db_table)} DISABLE ROW LEVEL SECURITY"
                    )
            cursor.execute("ALTER TABLE django_migrations DISABLE ROW LEVEL SECURITY")
