import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)


def emit_trace(endpoint: str, log_trace_to_console: bool = False):
    span_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=True,
    )
    resource = Resource.create(attributes={"service.name": "tracegen"})
    provider = TracerProvider(resource=resource)
    if log_trace_to_console:
        processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
    span_processor = BatchSpanProcessor(span_exporter)
    provider.add_span_processor(span_processor)
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("foo"):
        with tracer.start_as_current_span("bar"):
            with tracer.start_as_current_span("baz"):
                time.sleep(.1)


if __name__ == '__main__':
    import os

    emit_trace(os.getenv("TEMPO", "http://127.0.0.1:8080"))
