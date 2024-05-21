import os
import time
from pathlib import Path
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)


def emit_trace(
        endpoint: str,
        log_trace_to_console: bool = False,
        cert: Path = None,
        protocol: Literal["grpc", "http", "ALL"] = "grpc",
        nonce: Any = None
):
    os.environ['OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE'] = str(Path(cert).absolute()) if cert else ""

    if protocol == "grpc":
        span_exporter = GRPCExporter(
            endpoint=endpoint,
            insecure=not cert,
        )
    elif protocol == "http":
        span_exporter = HTTPExporter(
            endpoint=endpoint,
        )
    else:  # ALL
        return (emit_trace(endpoint, log_trace_to_console, cert, "grpc", nonce=nonce) and
                emit_trace(endpoint, log_trace_to_console, cert, "http", nonce=nonce))

    return _export_trace(span_exporter, log_trace_to_console=log_trace_to_console, nonce=nonce)


def _export_trace(span_exporter, log_trace_to_console: bool = False, nonce: Any = None):
    resource = Resource.create(attributes={
        "service.name": "tracegen",
        "nonce": str(nonce)
    }
    )
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

    return span_exporter.force_flush()


if __name__ == '__main__':
    emit_trace(
        endpoint=os.getenv("TRACEGEN_ENDPOINT", "http://127.0.0.1:8080"),
        cert=os.getenv("TRACEGEN_CERT", None),
        log_trace_to_console=os.getenv("TRACEGEN_VERBOSE", False),
        protocol=os.getenv("TRACEGEN_PROTOCOL", "http"),
        nonce=os.getenv("TRACEGEN_NONCE", "24")
    )
