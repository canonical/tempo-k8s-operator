# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from tempo import Tempo

import logging
import unittest
from unittest.mock import patch

import yaml
from ops.model import ActiveStatus
from ops.testing import Harness

from charm import TempoCharm

CONTAINER_NAME = "tempo"


class TestTempoCharm(unittest.TestCase):
    @patch("charm.KubernetesServicePatch", lambda x, y: None)
    def setUp(self):
        self.harness = Harness(TempoCharm)
        self.harness.set_model_name("testmodel")
        self.addCleanup(self.harness.cleanup)
        self.harness.set_leader(True)
        self.harness.begin_with_initial_hooks()
        self.maxDiff = None  # we're comparing big traefik configs in tests

    def test_tempo_pebble_ready(self):
        service = self._container.get_service("tempo")
        self.assertTrue(service.is_running())
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    def test_entrypoints_are_generated_with_sanitized_names(self):
        expected_entrypoints = {
            "entryPoints": {
                "tempo-http": {"address": ":3200"},
                "zipkin": {"address": ":9411"},
                "otlp-grpc": {"address": ":4317"},
                "otlp-http": {"address": ":4318"},
                "jaeger-thrift-http": {"address": ":14268"},
            }
        }
        self.assertEqual(self.harness.charm._static_ingress_config, expected_entrypoints)

    @patch("socket.getfqdn", lambda: "1.2.3.4")
    def test_ingress_relation_is_set_with_dynamic_config(self):
        self.harness.set_leader(True)
        self.harness.container_pebble_ready("tempo")
        rel_id = self.harness.add_relation("ingress", "traefik")
        self.harness.add_relation_unit(rel_id, "traefik/0")

        expected_rel_data = {
            "tcp": {
                "routers": {
                    "juju-testmodel-tempo-k8s-otlp-grpc": {
                        "entryPoints": ["otlp-grpc"],
                        "rule": "ClientIP(`0.0.0.0/0`)",
                        "service": "juju-testmodel-tempo-k8s-service-otlp-grpc",
                    }
                },
                "services": {
                    "juju-testmodel-tempo-k8s-service-otlp-grpc": {
                        "loadBalancer": {"servers": [{"address": "1.2.3.4:4317"}]}
                    }
                },
            },
            "http": {
                "routers": {
                    "juju-testmodel-tempo-k8s-jaeger-thrift-http": {
                        "entryPoints": ["jaeger-thrift-http"],
                        "rule": "ClientIP(`0.0.0.0/0`)",
                        "service": "juju-testmodel-tempo-k8s-service-jaeger-thrift-http",
                    },
                    "juju-testmodel-tempo-k8s-otlp-http": {
                        "entryPoints": ["otlp-http"],
                        "rule": "ClientIP(`0.0.0.0/0`)",
                        "service": "juju-testmodel-tempo-k8s-service-otlp-http",
                    },
                    "juju-testmodel-tempo-k8s-tempo-http": {
                        "entryPoints": ["tempo-http"],
                        "rule": "ClientIP(`0.0.0.0/0`)",
                        "service": "juju-testmodel-tempo-k8s-service-tempo-http",
                    },
                    "juju-testmodel-tempo-k8s-zipkin": {
                        "entryPoints": ["zipkin"],
                        "rule": "ClientIP(`0.0.0.0/0`)",
                        "service": "juju-testmodel-tempo-k8s-service-zipkin",
                    },
                },
                "services": {
                    "juju-testmodel-tempo-k8s-service-jaeger-thrift-http": {
                        "loadBalancer": {"servers": [{"url": "http://1.2.3.4:14268"}]}
                    },
                    "juju-testmodel-tempo-k8s-service-otlp-http": {
                        "loadBalancer": {"servers": [{"url": "http://1.2.3.4:4318"}]}
                    },
                    "juju-testmodel-tempo-k8s-service-tempo-http": {
                        "loadBalancer": {"servers": [{"url": "http://1.2.3.4:3200"}]}
                    },
                    "juju-testmodel-tempo-k8s-service-zipkin": {
                        "loadBalancer": {"servers": [{"url": "http://1.2.3.4:9411"}]}
                    },
                },
            },
        }

        rel_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)

        # yaml needs to be parsed as seen in grafana tests:
        # https://github.com/canonical/grafana-k8s-operator/blob/main/tests/unit/test_charm.py#L348
        self.assertEqual(yaml.safe_load(rel_data["config"]), expected_rel_data)

    def test_tracing_relation_updates_protocols_as_requested(self):
        self.harness.set_leader(True)
        self.harness.container_pebble_ready("tempo")

        tracing_rel_id = self.harness.add_relation("tracing", "grafana")
        self.harness.add_relation_unit(tracing_rel_id, "grafana/0")
        self.harness.update_relation_data(
            tracing_rel_id, "grafana", {"receivers": '["otlp_http"]'}
        )

        rel_data = self.harness.get_relation_data(tracing_rel_id, self.harness.charm.app.name)
        logging.warning(rel_data)
        self.assertEqual(rel_data["receivers"], '[{"protocol": "otlp_http", "port": 4318}]')

    @property
    def _container(self):
        return self.harness.model.unit.get_container(CONTAINER_NAME)

    @property
    def _plan(self):
        return self.harness.get_container_pebble_plan(CONTAINER_NAME)



@pytest.mark.parametrize(
    "protocols, expected_config",
    (
        (
            (
                "otlp_grpc",
                "otlp_http",
                "zipkin",
                "tempo",
                "jaeger_http_thrift",
                "jaeger_grpc",
                "jaeger_thrift_http",
                "jaeger_thrift_http",
            ),
            {
                "jaeger": {
                    "protocols": {
                        "grpc": None,
                        "thrift_http": None,
                    }
                },
                "zipkin": None,
                "otlp": {"protocols": {"http": None, "grpc": None}},
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": None,
                    }
                },
                "zipkin": None,
                "otlp": {"protocols": {"http": None}},
            },
        ),
        ([], {}),
    ),
)
def test_tempo_receivers_config(protocols, expected_config):
    assert Tempo(None)._build_receivers_config(protocols) == expected_config
