import time
from pathlib import Path
from typing import Literal

from grpc import ChannelCredentials
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as GRPCExporter
)
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
        protocol: Literal["grpc", "http", "ALL"] = "grpc"
):
    if protocol == "grpc":
        span_exporter = GRPCExporter(
            endpoint=endpoint,
            insecure=not cert,
            credentials=ChannelCredentials(cert) if cert else None
        )
    elif protocol == "http":
        span_exporter = HTTPExporter(
            endpoint=endpoint,
            certificate_file=str(Path(cert).absolute()) if cert else None,
        )
    else:  # ALL
        emit_trace(endpoint, log_trace_to_console, cert, "grpc")
        emit_trace(endpoint, log_trace_to_console, cert, "http")
        return

    _export_trace(span_exporter, log_trace_to_console=log_trace_to_console)


def _export_trace(span_exporter, log_trace_to_console: bool = False, ):
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

    emit_trace(
        endpoint=os.getenv("TRACEGEN_ENDPOINT", "http://127.0.0.1:8080"),
        cert=os.getenv("TRACEGEN_CERT", None),
        log_trace_to_console=os.getenv("TRACEGEN_VERBOSE", False),
        protocol=os.getenv("TRACEGEN_PROTOCOL", "ALL")
    )
