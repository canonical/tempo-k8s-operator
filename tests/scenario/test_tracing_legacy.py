import json
import socket

import pytest
from charm import TempoCharm
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v1.tracing import TracingProviderAppData as TracingProviderAppDataV1
from charms.tempo_k8s.v2.tracing import TracingProviderAppData as TracingProviderAppDataV2
from scenario import Container, Relation, State

NO_RECEIVERS = 13
"""Number of supported receivers (incl. deprecated legacy ones)."""
NO_RECEIVERS_LEGACY = 6
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
    assert tracing_out.local_app_data == {
        "ingesters": '[{"protocol": "tempo", "port": 3201}, '
        '{"protocol": "otlp_grpc", "port": 4317}, '
        '{"protocol": "otlp_http", "port": 4318}, '
        '{"protocol": "zipkin", "port": 9411}, '
        '{"protocol": "jaeger_http_thrift", "port": 14269}, '
        '{"protocol": "jaeger_grpc", "port": 14250}]',
        "host": json.dumps(socket.getfqdn()),
    }


@pytest.mark.parametrize("evt_name", ("changed", "created", "joined"))
def test_tracing_v2_endpoint_published(context, evt_name, base_state):
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    state = base_state.replace(relations=[tracing])

    with charm_tracing_disabled():
        with context.manager(getattr(tracing, f"{evt_name}_event"), state) as mgr:
            assert len(mgr.charm._requested_receivers()) == 0
            out = mgr.run()

    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "receivers": "[]",
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
    tracing_v2 = Relation("tracing", remote_app_data={"receivers": json.dumps(["tempo_http"])})
    state = base_state.replace(relations=[tracing_v1, tracing_v2])

    with charm_tracing_disabled():
        with context.manager(tracing_v1.changed_event, state) as mgr:
            charm: TempoCharm = mgr.charm
            # all receivers active, because we have a v1 relation active
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
