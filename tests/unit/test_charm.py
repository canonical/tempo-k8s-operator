# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import socket
import unittest
from unittest.mock import patch

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
                "tempo-grpc": {"address": ":9096"},
                "zipkin": {"address": ":9411"},
                "otlp-grpc": {"address": ":4317"},
                "otlp-http": {"address": ":4318"},
                "jaeger-thrift-http": {"address": ":14268"},
            }
        }
        self.assertEqual(self.harness.charm._static_ingress_config, expected_entrypoints)

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
        self.assertEqual(
            rel_data["receivers"],
            f'[{{"protocol": {{"name": "otlp_http", "type": "http"}}, "url": "http://{socket.getfqdn()}:4318"}}]',
        )

    @property
    def _container(self):
        return self.harness.model.unit.get_container(CONTAINER_NAME)

    @property
    def _plan(self):
        return self.harness.get_container_pebble_plan(CONTAINER_NAME)
