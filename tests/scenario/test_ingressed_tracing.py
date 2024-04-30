import json
import socket

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import Container, Relation, State


@pytest.fixture
def base_state():
    return State(leader=True, containers=[Container("tempo", can_connect=False)])


def test_external_url_present(context, base_state):
    # WHEN ingress is related with external_host
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    ingress = Relation("ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"})
    state = base_state.replace(relations=[tracing, ingress])

    with charm_tracing_disabled():
        with context.manager(getattr(tracing, "created_event"), state) as mgr:
            out = mgr.run()

    # THEN external_url is present in tracing relation databag
    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "receivers": '[{"protocol": "otlp_http", "port": 4318}]',
        "host": json.dumps(socket.getfqdn()),
        "external_url": '"http://1.2.3.4"',
    }
