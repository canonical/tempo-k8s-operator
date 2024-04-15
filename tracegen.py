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

span_exporter = OTLPSpanExporter(
    # endpoint="http://tempo-ip:8080/otlp_grpc",
    endpoint="http://127.0.0.1:8080/otlp_grpc",
    insecure=True,
)
resource = Resource.create(attributes={"service.name": "tracegen"})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(ConsoleSpanExporter())
span_processor = BatchSpanProcessor(span_exporter)
provider.add_span_processor(processor)
provider.add_span_processor(span_processor)
trace.set_tracer_provider(provider)


tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("foo"):
    with tracer.start_as_current_span("bar"):
        with tracer.start_as_current_span("baz"):
            print("Hello world from OpenTelemetry Python!")
