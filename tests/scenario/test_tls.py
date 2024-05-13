import socket
from unittest.mock import patch

import pytest
from charm import TempoCharm
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from charms.tempo_k8s.v2.tracing import TracingProviderAppData, TracingRequirerAppData
from scenario import Container, Relation, State


@pytest.fixture
def base_state():
    return State(leader=True, containers=[Container("tempo", can_connect=False)])


@pytest.mark.parametrize("remote_has_tls", (True, False))
@pytest.mark.parametrize("local_has_tls", (True, False))
@pytest.mark.parametrize("has_ingress", (True, False))
def test_tracing_endpoints_with_tls(
    context, base_state, has_ingress, local_has_tls, remote_has_tls
):
    tracing = Relation(
        "tracing",
        remote_app_data=TracingRequirerAppData(receivers=["otlp_http"]).dump(),
    )
    relations = [tracing]

    local_scheme = "https" if local_has_tls else "http"
    remote_scheme = "https" if remote_has_tls else "http"

    if has_ingress:
        relations.append(
            Relation(
                "ingress",
                remote_app_data={"scheme": remote_scheme, "external_host": "foo.com.org"},
            )
        )

    state = base_state.replace(relations=relations)

    with charm_tracing_disabled(), patch.object(TempoCharm, "tls_available", local_has_tls):
        out = context.run(tracing.changed_event, state)

    tracing_provider_app_data = TracingProviderAppData.load(
        out.get_relations(tracing.endpoint)[0].local_app_data
    )
    assert tracing_provider_app_data.host == socket.getfqdn()
    assert (
        tracing_provider_app_data.external_url
        == f"{remote_scheme if has_ingress else local_scheme}://{socket.getfqdn() if not has_ingress else 'foo.com.org'}"
    )
    assert tracing_provider_app_data.internal_scheme == local_scheme
