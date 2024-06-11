import socket

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v2.tracing import (
    EndpointChangedEvent,
    EndpointRemovedEvent,
    ProtocolNotRequestedError,
    TracingEndpointRequirer,
)
from ops import CharmBase, Framework, RelationBrokenEvent, RelationChangedEvent
from scenario import Context, Relation, State

from tempo import Tempo


class MyCharm(CharmBase):
    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.tracing = TracingEndpointRequirer(self, protocols=["otlp_grpc"])
        framework.observe(self.tracing.on.endpoint_changed, self._on_endpoint_changed)

    def _on_endpoint_changed(self, e):
        pass


@pytest.fixture
def context():
    return Context(
        charm_type=MyCharm,
        meta={"name": "jolly", "requires": {"tracing": {"interface": "tracing", "limit": 1}}},
    )


def test_requirer_api(context):
    host = socket.getfqdn()
    tracing = Relation(
        "tracing",
        remote_app_data={
            "receivers": f'[{{"protocol": {{"name": "otlp_grpc", "type": "grpc"}}, "url": "{host}:4317"}}, '
            f'{{"protocol": {{"name": "otlp_http", "type": "http"}}, "url": "http://{host}:4318"}}, '
            f'{{"protocol": {{"name": "zipkin", "type": "http"}}, "url": "http://{host}:9411" }}]',
        },
    )
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.changed_event, state) as mgr:
            charm = mgr.charm
            assert charm.tracing.get_endpoint("otlp_grpc") == f"{host}:4317"
            assert charm.tracing.get_endpoint("otlp_http") == f"http://{host}:4318"
            assert charm.tracing.get_endpoint("zipkin") == f"http://{host}:9411"

            rel = charm.model.get_relation("tracing")
            assert charm.tracing.is_ready(rel)

    rchanged, epchanged = context.emitted_events
    assert isinstance(epchanged, EndpointChangedEvent)
    assert epchanged.receivers[0].protocol.name == "otlp_grpc"
    assert epchanged.receivers[1].protocol.name == "otlp_http"
    assert epchanged.receivers[2].protocol.name == "zipkin"


def test_requirer_api_with_internal_scheme(context):
    host = socket.getfqdn()
    tracing = Relation(
        "tracing",
        remote_app_data={
            "receivers": f'[{{"protocol": {{"name": "otlp_grpc", "type": "grpc"}} , "url": "{host}:4317"}}, '
            f'{{"protocol": {{"name": "otlp_http", "type": "http"}}, "url": "https://{host}:4318"}}, '
            f'{{"protocol": {{"name": "zipkin", "type": "http"}}, "url":  "https://{host}:9411"}}]',
        },
    )
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.changed_event, state) as mgr:
            charm = mgr.charm
            assert charm.tracing.get_endpoint("otlp_grpc") == f"{host}:4317"
            assert charm.tracing.get_endpoint("otlp_http") == f"https://{host}:4318"
            assert charm.tracing.get_endpoint("zipkin") == f"https://{host}:9411"

            rel = charm.model.get_relation("tracing")
            assert charm.tracing.is_ready(rel)

    rchanged, epchanged = context.emitted_events
    assert isinstance(epchanged, EndpointChangedEvent)
    assert epchanged.receivers[0].protocol.name == "otlp_grpc"


def test_ingressed_requirer_api(context):
    # WHEN external_url is present in remote app databag
    external_url = "http://1.2.3.4"
    tracing = Relation(
        "tracing",
        remote_app_data={
            "receivers": f'[{{"protocol": {{"name": "otlp_grpc", "type": "grpc"}}, "url": "{external_url.split("://")[1]}:4317" }}, '
            f'{{"protocol": {{"name": "otlp_http", "type": "http"}} , "url": "{external_url}:4318" }}, '
            f'{{"protocol": {{"name": "zipkin", "type": "http"}} , "url": "{external_url}:9411" }}]',
        },
    )
    state = State(leader=True, relations=[tracing])

    # THEN get_endpoint uses external URL instead of the host
    with charm_tracing_disabled():
        with context.manager(tracing.changed_event, state) as mgr:
            charm = mgr.charm
            assert (
                charm.tracing.get_endpoint("otlp_grpc")
                == f"{external_url.split('://')[1]}:{Tempo.receiver_ports['otlp_grpc']}"
            )
            for proto in ["otlp_http", "zipkin"]:
                assert (
                    charm.tracing.get_endpoint(proto)
                    == f"{external_url}:{Tempo.receiver_ports[proto]}"
                )

            rel = charm.model.get_relation("tracing")
            assert charm.tracing.is_ready(rel)

    rchanged, epchanged = context.emitted_events
    assert isinstance(epchanged, EndpointChangedEvent)
    assert epchanged.receivers[0].protocol.name == "otlp_grpc"


@pytest.mark.parametrize(
    "data",
    (
        {
            "ingesters": '[{"protocol": "otlp_grpc", "port": 9999}]',
            "bar": "baz",
        },
        {
            "host": "foo.com",
            "bar": "baz",
        },
        {
            "ingesters": '[{"burp": "barp", "port": 3200}]',
            "host": "foo.com",
        },
        {
            "ingesters": '[{"protocol": "tempo", "burp": "borp"}]',
            "host": "foo.com",
        },
    ),
)
def test_invalid_data(context, data):
    tracing = Relation(
        "tracing",
        remote_app_data=data,
    )
    state = State(leader=True, relations=[tracing])

    def post_event(charm: MyCharm):
        rel = charm.model.get_relation("tracing")
        assert not charm.tracing.is_ready(rel)

    with charm_tracing_disabled():
        context.run(tracing.changed_event, state, post_event=post_event)

    emitted_events = context.emitted_events
    assert len(emitted_events) == 2
    rchanged, rremoved = emitted_events
    assert isinstance(rchanged, RelationChangedEvent)
    assert isinstance(rremoved, EndpointRemovedEvent)


def test_broken(context):
    tracing = Relation("tracing")
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        context.run(tracing.broken_event, state)

    emitted_events = context.emitted_events
    assert len(emitted_events) == 2
    rchanged, ebroken = emitted_events
    assert isinstance(rchanged, RelationBrokenEvent)
    assert isinstance(ebroken, EndpointRemovedEvent)


def test_requested_not_yet_replied(context):
    tracing = Relation("tracing")
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.created_event, state) as mgr:
            charm = mgr.charm
            charm.tracing.request_protocols(["otlp_http"])
            charm.tracing.get_endpoint("otlp_http")


def test_not_requested_raises(context):
    tracing = Relation("tracing")
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.created_event, state) as mgr:
            charm = mgr.charm
            with pytest.raises(ProtocolNotRequestedError):
                charm.tracing.get_endpoint("otlp_http")
