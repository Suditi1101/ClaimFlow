"""Keep Django tables private from Supabase's separate REST API."""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("PostgreSQL is required.")
        tables = {
            model._meta.db_table
            for model in apps.get_models(include_auto_created=True)
            if model._meta.managed and not model._meta.proxy
        }
        tables.add("django_migrations")
        with connection.cursor() as cursor:
            existing = set(connection.introspection.table_names(cursor))
            cursor.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')"
            )
            roles = [row[0] for row in cursor.fetchall()]
            for table in sorted(tables & existing):
                name = connection.ops.quote_name(table)
                cursor.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
                for role in roles:
                    cursor.execute(
                        f"REVOKE ALL ON TABLE {name} FROM {connection.ops.quote_name(role)}"
                    )
        self.stdout.write("Django table access restricted to the database backend.")
