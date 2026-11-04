import os
from prometheus_client import Counter, Histogram

EVENTS = Counter("claimflow_events_total", "Event outcomes", ["role", "outcome"])
PUBLISH = Counter("claimflow_outbox_total", "Outbox delivery outcomes", ["outcome"])
DURATION = Histogram("claimflow_event_seconds", "Processing duration", ["role"])


def setup():
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", "claimflow")}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=5)))
    trace.set_tracer_provider(provider)
