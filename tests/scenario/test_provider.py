import json
import socket

import pytest
from charms.tempo_k8s.v0.charm_instrumentation import _charm_tracing_disabled
from charms.tempo_k8s.v0.tracing import EndpointChangedEvent, TracingEndpointProvider
from ops import CharmBase, Framework
from scenario import Context, Relation, State


class MyCharm(CharmBase):
    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.tracing = TracingEndpointProvider(self)
        framework.observe(self.tracing.on.endpoint_changed, self._on_endpoint_changed)

    def _on_endpoint_changed(self, e):
        pass


@pytest.fixture
def context():
    return Context(
        charm_type=MyCharm,
        meta={"name": "jolly", "provides": {"tracing": {"interface": "tracing"}}},
    )


def test_requirer_api(context):
    host = socket.getfqdn()
    tracing = Relation(
        "tracing",
        remote_app_data={
            "ingesters": '[{"protocol": "tempo", "port": 3200}, '
            '{"protocol": "otlp_grpc", "port": 4317}, '
            '{"protocol": "otlp_http", "port": 4318}, '
            '{"protocol": "zipkin", "port": 9411}]',
            "host": json.dumps(host),
        },
    )
    state = State(leader=True, relations=[tracing])

    def post_event(charm:MyCharm):
        assert charm.tracing.otlp_grpc_endpoint == f"{host}:4317"
        assert charm.tracing.otlp_http_endpoint == f"{host}:4318"
        assert charm.tracing.otlp_http_endpoint == f"{host}:4318"

    with _charm_tracing_disabled():
        out = context.run(tracing.changed_event, state,
                          post_event=post_event)

    rchanged, epchanged = context.emitted_events
    assert isinstance(epchanged, EndpointChangedEvent)
    assert epchanged.host == host
    assert epchanged.ingesters[0].protocol == "tempo"
