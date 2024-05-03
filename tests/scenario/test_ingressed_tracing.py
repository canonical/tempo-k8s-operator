import json
import socket
from unittest.mock import patch

import pytest
import yaml
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
        out = context.run(getattr(tracing, "created_event"), state)

    # THEN external_url is present in tracing relation databag
    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "receivers": '[{"protocol": "otlp_http", "port": 4318}]',
        "host": json.dumps(socket.getfqdn()),
        "external_url": '"http://1.2.3.4"',
        "internal_scheme": '"http"',
    }


@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_ingress_relation_set_with_dynamic_config(context, base_state):
    # WHEN ingress is related with external_host
    ingress = Relation("ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"})
    state = base_state.replace(relations=[ingress])

    out = context.run(getattr(ingress, "joined_event"), state)

    expected_rel_data = {
        "tcp": {
            "routers": {
                f"juju-{state.model.name}-tempo-k8s-otlp-grpc": {
                    "entryPoints": ["otlp-grpc"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-tempo-k8s-service-otlp-grpc",
                }
            },
            "services": {
                f"juju-{state.model.name}-tempo-k8s-service-otlp-grpc": {
                    "loadBalancer": {"servers": [{"address": "1.2.3.4:4317"}]}
                }
            },
        },
        "http": {
            "routers": {
                f"juju-{state.model.name}-tempo-k8s-jaeger-thrift-http": {
                    "entryPoints": ["jaeger-thrift-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-tempo-k8s-service-jaeger-thrift-http",
                },
                f"juju-{state.model.name}-tempo-k8s-otlp-http": {
                    "entryPoints": ["otlp-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-tempo-k8s-service-otlp-http",
                },
                f"juju-{state.model.name}-tempo-k8s-tempo-http": {
                    "entryPoints": ["tempo-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-tempo-k8s-service-tempo-http",
                },
                f"juju-{state.model.name}-tempo-k8s-zipkin": {
                    "entryPoints": ["zipkin"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-tempo-k8s-service-zipkin",
                },
            },
            "services": {
                f"juju-{state.model.name}-tempo-k8s-service-jaeger-thrift-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:14268"}]}
                },
                f"juju-{state.model.name}-tempo-k8s-service-otlp-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:4318"}]}
                },
                f"juju-{state.model.name}-tempo-k8s-service-tempo-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:3200"}]}
                },
                f"juju-{state.model.name}-tempo-k8s-service-zipkin": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:9411"}]}
                },
            },
        },
    }

    # THEN dynamic config is present in ingress relation
    ingress_out = out.get_relations(ingress.endpoint)[0]
    assert yaml.safe_load(ingress_out.local_app_data["config"]) == expected_rel_data
