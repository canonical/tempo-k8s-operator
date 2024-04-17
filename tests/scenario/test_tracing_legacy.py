import json
import socket

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v1.tracing import (
    TracingProviderAppData as TracingProviderAppDataV1,
)
from charms.tempo_k8s.v2.tracing import (
    TracingProviderAppData as TracingProviderAppDataV2,
)
from scenario import Container, Relation, State

from charm import LEGACY_RECEIVER_PROTOCOLS, TempoCharm
from tempo import Tempo

NO_RECEIVERS = 13
"""Number of supported receivers (incl. deprecated legacy ones)."""
NO_RECEIVERS_LEGACY = len(LEGACY_RECEIVER_PROTOCOLS)
"""Number of supported receivers in legacy v0/v1 tracing."""


@pytest.fixture
def base_state():
    return State(leader=True, containers=[Container("tempo", can_connect=False)])


def test_tracing_v1_endpoint_published(context, base_state):
    tracing = Relation(
        "tracing",
        remote_units_data={
            1: {"egress-subnets": "12", "ingress-address": "12", "private-address": "12"}
        },
    )
    state = base_state.replace(relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.changed_event, state) as mgr:
            out = mgr.run()

            charm = mgr.charm
            # we have v1 relations: we enable the legacy receivers
            assert len(charm._requested_receivers()) == NO_RECEIVERS_LEGACY

    tracing_out = out.get_relations(tracing.endpoint)[0]
    ingesters = json.loads(tracing_out.local_app_data["ingesters"])
    assert len(ingesters) == NO_RECEIVERS_LEGACY

    for ingester in ingesters:
        protocol = ingester["protocol"]
        assert ingester["port"] == Tempo.receiver_ports[protocol]

    assert json.loads(tracing_out.local_app_data["host"]) == socket.getfqdn()


@pytest.mark.parametrize("evt_name", ("changed", "created", "joined"))
def test_tracing_v2_endpoint_published(context, evt_name, base_state):
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    state = base_state.replace(relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(getattr(tracing, f"{evt_name}_event"), state) as mgr:
            assert len(mgr.charm._requested_receivers()) == 1
            out = mgr.run()

    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "receivers": '[{"protocol": "otlp_http", "port": 4318}]',
        "host": json.dumps(socket.getfqdn()),
    }


@pytest.mark.parametrize("evt_name", ("changed", "created", "joined"))
def test_tracing_v1_then_v2_endpoint_published(context, evt_name, base_state):
    tracing = Relation("tracing")
    state = base_state.replace(relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(tracing.changed_event, state) as mgr:
            charm: TempoCharm = mgr.charm
            # all receivers active, because we have a v1 relation active
            assert len(charm._requested_receivers()) == NO_RECEIVERS_LEGACY

    tracing_out = mgr.output.get_relations(tracing.endpoint)[0]
    assert (
        len(TracingProviderAppDataV1.load(tracing_out.local_app_data).ingesters)
        == NO_RECEIVERS_LEGACY
    )

    tracing_v2 = Relation("tracing", remote_app_data={"receivers": json.dumps(["tempo_http"])})
    # now it becomes clear the tracing relation is in fact v2
    state2 = base_state.replace(relations=[tracing_v2])

    with charm_tracing_disabled():
        with context.manager(getattr(tracing_v2, f"{evt_name}_event"), state2) as mgr:
            charm: TempoCharm = mgr.charm
            # only tempo_http is requested
            assert len(charm._requested_receivers()) == 1

    tracing_v2_out = mgr.output.get_relations(tracing.endpoint)[0]
    assert len(TracingProviderAppDataV2.load(tracing_v2_out.local_app_data).receivers) == 1


def test_tracing_v1_and_v2_endpoints_published(context, base_state):
    tracing_v1 = Relation("tracing")
    tracing_v2 = Relation(
        "tracing", remote_app_data={"receivers": json.dumps(["jaeger_thrift_http"])}
    )
    state = base_state.replace(relations=[tracing_v1, tracing_v2])

    with charm_tracing_disabled():
        # on created, we publish v1 legacy receivers to existing v2 relations as well
        after_created = context.run(tracing_v1.created_event, state)

        with context.manager(tracing_v1.changed_event, after_created) as mgr:
            charm: TempoCharm = mgr.charm
            # all legacy receivers active, because we have a v1 relation active, and on
            # top of that the jaeger_thrift_http that the v2 relation requested
            assert len(charm._requested_receivers()) == NO_RECEIVERS_LEGACY + 1

    tracing_v1_out, tracing_v2_out = mgr.output.get_relations(tracing_v1.endpoint)

    # both relations publish all receivers, but v1 only sees the legacy ones.
    assert (
        len(TracingProviderAppDataV1.load(tracing_v1_out.local_app_data).ingesters)
        == NO_RECEIVERS_LEGACY
    )
    assert (
        len(TracingProviderAppDataV2.load(tracing_v2_out.local_app_data).receivers)
        == NO_RECEIVERS_LEGACY + 1
    )


@pytest.mark.parametrize("first_v1", (True, False))
def test_tracing_legacy_receivers_publish(context, base_state, first_v1):
    # Test that regardless of the creation order of v1 and v2 relations,
    # the receivers are correctly published and up to date.

    tracing_v1 = Relation("tracing")
    # in tracing v2, the remote end has requested an endpoint that isn't legacy
    tracing_v2 = Relation(
        "tracing", remote_app_data={"receivers": json.dumps(["jaeger_thrift_http"])}
    )

    if first_v1:
        first, second = tracing_v1, tracing_v2
    else:
        first, second = tracing_v2, tracing_v1

    state = base_state.replace(relations=[first])

    with charm_tracing_disabled():
        # we create a
        seq = [first.created_event, first.joined_event, first.changed_event]
        for e in seq:
            state = context.run(e, state)

        # then we create the other one:
        state = state.replace(relations=state.relations + [second])
        seq = [second.created_event, second.joined_event, second.changed_event]
        for e in seq:
            state = context.run(e, state)

    rel1, rel2 = state.get_relations("tracing")
    if rel1.remote_app_data:
        tracing_v2_out, tracing_v1_out = rel1, rel2
    else:
        tracing_v2_out, tracing_v1_out = rel2, rel1

    v1_protocols = {
        i.protocol for i in TracingProviderAppDataV1.load(tracing_v1_out.local_app_data).ingesters
    }
    v2_protocols = {
        r.protocol for r in TracingProviderAppDataV2.load(tracing_v2_out.local_app_data).receivers
    }

    # both relations published all receivers, but v1 only sees the legacy ones.
    assert v1_protocols == {"otlp_grpc", "otlp_http", "zipkin"}
    assert v2_protocols == {"otlp_grpc", "otlp_http", "zipkin", "jaeger_thrift_http"}
