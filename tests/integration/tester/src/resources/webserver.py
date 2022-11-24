# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import os
import random
import time

import requests
import uvicorn as uvicorn
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import \
    OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.types import ASGIApp


APP_NAME = os.environ.get('APP_NAME')
APP = os.environ.get('APP_NAME')
TEMPO_ENDPOINT = os.environ.get('TEMPO_ENDPOINT')
HOST = os.environ.get('HOST')
PORT = int(os.environ.get('PORT'))


def init_master():
    # manual instrumentation for master node
    resource = Resource.create(attributes={
        "service.name": APP_NAME,
        "compose_service": APP_NAME
    })

    provider = TracerProvider(resource=resource)
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=TEMPO_ENDPOINT))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(__name__)

    slaves = os.environ.get('PEERS').split(';')
    while True:
        with tracer.start_as_current_span("query_peers") as span:
            for i, slave in enumerate(slaves):
                url = slave + f'/query/{i}'

                print(f'sending {i} to {slave}')
                span.add_event("log", {
                    "slave.id": i,
                    "slave.root_url": slave,
                    "slave.query_url": slave,
                })

                with tracer.start_as_current_span(f"query_peer_{i}") as child_span:
                    resp = requests.get(url)
                    print(f'received {resp} from {slave}')
                    try:
                        id_, value_ = resp.text.split('=')
                    except:
                        id_ = value_ = None
                        print(f'error handling response from {url}: invalid text: {resp.text}')

                    child_span.add_event("log", {
                        "slave.response.id": id_,
                        "slave.response.value": value_,
                    })


def init_slave():
    # auto instrumentation for slave nodes
    app = FastAPI()

    @app.get("/query/{var}")
    async def query(var: str):
        randn = random.random()
        time.sleep(randn)
        return f"{var}={randn}"

    def setup_otlp(app: ASGIApp, app_name: str, endpoint: str, log_correlation: bool = True) -> None:
        # Setting OpenTelemetry
        # set the service name to show in traces
        resource = Resource.create(attributes={
            "service.name": app_name,
            "compose_service": app_name
        })

        # set the tracer provider
        tracer = TracerProvider(resource=resource)
        trace.set_tracer_provider(tracer)

        tracer.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint)))

        if log_correlation:
            LoggingInstrumentor().instrument(set_logging_format=True)

        FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer)

    setup_otlp(app, APP_NAME, TEMPO_ENDPOINT)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    if os.environ.get('MASTER'):
        init_master()
    else:
        init_slave()
