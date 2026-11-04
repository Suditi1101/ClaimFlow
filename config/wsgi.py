import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from common.telemetry import setup

    setup()
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    DjangoInstrumentor().instrument()
from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
