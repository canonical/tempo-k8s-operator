# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import random
import time
from typing import Tuple

import requests
import uvicorn as uvicorn
from fastapi import FastAPI
from fastapi.openapi.models import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.openmetrics.exposition import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.routing import Match
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
from starlette.types import ASGIApp

REQUESTS_PROCESSING_TIME = Histogram(
    "fastapi_requests_duration_seconds",
    "Histogram of requests processing time by path (in seconds)",
    ["method", "path", "app_name"],
)

APP_NAME = os.environ.get("APP_NAME")
TEMPO_ENDPOINT = os.environ.get("TEMPO_ENDPOINT", "")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 3000))


if not TEMPO_ENDPOINT:
    logging.warning("TEMPO_ENDPOINT not configured; tracing disabled. webserver dead.")


INFO = Gauge("fastapi_app_info", "application information.", ["app_name"])
REQUESTS = Counter(
    f"{APP_NAME}_requests_total",
    "Total count of requests by method and path.",
    ["method", "path", "app_name"],
)
RESPONSES = Counter(
    f"{APP_NAME}_responses_total",
    "Total count of responses by method, path and status codes.",
    ["method", "path", "status_code", "app_name"],
)
REQUESTS_PROCESSING_TIME = Histogram(
    f"{APP_NAME}_requests_duration_seconds",
    "Histogram of requests processing time by path (in seconds)",
    ["method", "path", "app_name"],
)
EXCEPTIONS = Counter(
    f"{APP_NAME}_exceptions_total",
    "Total count of exceptions raised by path and exception type",
    ["method", "path", "exception_type", "app_name"],
)
REQUESTS_IN_PROGRESS = Gauge(
    f"{APP_NAME}_requests_in_progress",
    "Gauge of requests by method and path currently being processed",
    ["method", "path", "app_name"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, app_name: str = APP_NAME) -> None:
        super().__init__(app)
        self.app_name = app_name
        INFO.labels(app_name=self.app_name).inc()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        method = request.method
        path, is_handled_path = self.get_path(request)

        if not is_handled_path:
            return await call_next(request)

        REQUESTS_IN_PROGRESS.labels(method=method, path=path, app_name=self.app_name).inc()
        REQUESTS.labels(method=method, path=path, app_name=self.app_name).inc()
        before_time = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as e:
            status_code = HTTP_500_INTERNAL_SERVER_ERROR
            EXCEPTIONS.labels(
                method=method, path=path, exception_type=type(e).__name__, app_name=self.app_name
            ).inc()
            raise e from None
        else:
            status_code = response.status_code
            after_time = time.perf_counter()
            # retrieve trace id for exemplar
            span = trace.get_current_span()
            trace_id = trace.format_trace_id(span.get_span_context().trace_id)

            REQUESTS_PROCESSING_TIME.labels(
                method=method, path=path, app_name=self.app_name
            ).observe(after_time - before_time, exemplar={"TraceID": trace_id})
        finally:
            RESPONSES.labels(
                method=method, path=path, status_code=status_code, app_name=self.app_name
            ).inc()
            REQUESTS_IN_PROGRESS.labels(method=method, path=path, app_name=self.app_name).dec()

        return response

    @staticmethod
    def get_path(request: Request) -> Tuple[str, bool]:
        for route in request.app.routes:
            match, child_scope = route.matches(request.scope)
            if match == Match.FULL:
                return route.path, True

        return request.url.path, False


def init_overlord():
    logging.debug("Initing as overlord.")
    # manual instrumentation for overlord node
    resource = Resource.create(attributes={"service.name": APP_NAME, "compose_service": APP_NAME})

    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=TEMPO_ENDPOINT))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)
    subordinates = os.environ.get("PEERS").split(";")

    print(f"preparing to send traces to {subordinates}")

    while True:
        with tracer.start_as_current_span(
            "query_peers", attributes={"service.name": APP_NAME}
        ) as span:
            for i, subordinate in enumerate(subordinates):
                url = "http://" + subordinate + f":{PORT}/query/{i}"

                print(f"sending {i} to {subordinate}")
                span.add_event(
                    "log",
                    {
                        "service.name": APP_NAME,
                        "subordinate.id": i,
                        "subordinate.root_url": subordinate,
                        "subordinate.query_url": subordinate,
                    },
                )

                with tracer.start_as_current_span(
                    f"query_peer_{i}", attributes={"service.name": APP_NAME}
                ) as child_span:
                    headers = {}
                    inject(headers)  # inject trace id into request headers.

                    resp = requests.get(url, headers=headers)
                    print(f"received {resp} from {subordinate}")
                    try:
                        id_, value_ = resp.text.split("=")
                    except:  # noqa
                        id_ = value_ = None
                        print(f"error handling response from {url}: invalid text: {resp.text}")

                    child_span.add_event(
                        "log",
                        {
                            "service.name": APP_NAME,
                            "subordinate.response.id": id_,
                            "subordinate.response.value": value_,
                        },
                    )
        provider.force_flush()


def init_subordinate():
    logging.debug("Initing as subordinate.")
    # auto instrumentation for subordinate nodes
    app = FastAPI()
    app.add_middleware(PrometheusMiddleware, app_name=APP_NAME)

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(REGISTRY), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.get("/query/{var}")
    async def query(var: str):
        randn = random.random()
        time.sleep(randn)

        # add request processing time metric with exemplar
        span = trace.get_current_span()
        trace_id = trace.format_trace_id(span.get_span_context().trace_id)
        REQUESTS_PROCESSING_TIME.labels(
            method="GET", path="/query/{var}", app_name=APP_NAME
        ).observe(randn, exemplar={"TraceID": trace_id})
        return f"{var}={randn}"

    def setup_otlp(
        app: ASGIApp, app_name: str, endpoint: str, log_correlation: bool = True
    ) -> None:
        # Setting OpenTelemetry
        # set the service name to show in traces
        resource = Resource.create(
            attributes={"service.name": app_name, "compose_service": app_name}
        )

        # set the tracer provider
        tracer = TracerProvider(resource=resource)
        trace.set_tracer_provider(tracer)

        tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

        if log_correlation:
            LoggingInstrumentor().instrument(set_logging_format=True)

        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer)

    setup_otlp(app, APP_NAME, TEMPO_ENDPOINT)
    uvicorn.run(app, host=HOST, port=PORT)


class EndpointFilter(logging.Filter):
    # Uvicorn endpoint access log filter
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("GET /metrics") == -1


# Filter out /endpoint
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


if __name__ == "__main__":
    if not TEMPO_ENDPOINT:
        while True:
            logging.warning("IDLING. TEMPO_ENDPOINT unavailable.")
            time.sleep(10)

    if os.environ.get("OVERLORD"):
        init_overlord()
    else:
        init_subordinate()
