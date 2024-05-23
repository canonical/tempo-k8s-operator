import socket

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import Container, Relation, State


@pytest.fixture
def base_state():
    return State(leader=True, containers=[Container("tempo", can_connect=False)])


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
        "receivers": f'[{{"protocol": {{"name": "otlp_http", "type": "http"}}, "url": "http://{socket.getfqdn()}:4318"}}]',
    }
