#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight elasticsearch alternative."""

import logging
import re
from typing import Optional

from ops.charm import CharmBase, WorkloadEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus
from ops.pebble import Layer

from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo.v0.tempo_scrape import TracingEndpointRequirer
from tempo import Tempo

logger = logging.getLogger(__name__)


class TempoCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    _stored = StoredState()
    _log_path = "/var/log/tempo.log"

    def __init__(self, *args):
        super().__init__(*args)
        self._stored.set_default(initial_admin_password="")
        self.framework.observe(self.on.tempo_pebble_ready, self._on_tempo_pebble_ready)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.tempo = tempo = Tempo()

        # # Patch the juju-created Kubernetes service to contain the right ports
        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml
        self._service_patcher = KubernetesServicePatch(
            self, tempo.get_requested_ports(self.app.name))

        # Provide ability for Tempo to be scraped by Prometheus using prometheus_scrape
        self._scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": [f"*:{tempo.tempo_port}"]}]}],
        )

        # Enable log forwarding for Loki and other charms that implement loki_push_api
        self._logging = LogProxyConsumer(self, relation_name="logging",
                                         log_files=[self._log_path])

        # Provide grafana dashboards over a relation interface
        # self._grafana_dashboards = GrafanaDashboardProvider(
        #     self, relation_name="grafana-dashboard"
        # )

        # Enable profiling over a relation with Parca
        # self._profiling = ProfilingEndpointProvider(
        #     self, jobs=[{"static_configs": [{"targets": ["*:4080"]}]}]
        # )

        self._tracing = TracingEndpointRequirer(
            self, tempo_host=tempo.host, ingesters=tempo.ingesters
        )
        # self._ingress = IngressPerAppRequirer(self, port=4080)

    def _on_tempo_pebble_ready(self, event: WorkloadEvent):
        container = event.workload

        if not container.can_connect():
            return event.defer()

        # drop tempo_config.yaml into the container
        container.push(self.tempo.config_path, self.tempo.get_config())

        container.add_layer("tempo", self._pebble_layer, combine=True)
        container.replan()

        self.unit.set_workload_version(self.version)
        self.unit.status = ActiveStatus()

    def _on_update_status(self, _):
        """Update the status of the application."""
        self.unit.set_workload_version(self.version)

    @property
    def _pebble_layer(self) -> Layer:
        return Layer(
            {
                "services": {
                    "tempo": {
                        "override": "replace",
                        "summary": "tempo",
                        "command": '/bin/sh -c "/tempo -config.file={} | tee {}"'.format(
                            self.tempo.config_path,
                            self._log_path),
                        "startup": "enabled",
                    }
                },
            }
        )

    @property
    def version(self) -> str:
        """Reports the current Tempo version."""
        container = self.unit.get_container("tempo")
        if container.can_connect() and container.get_services("tempo"):
            try:
                return self._get_version() or ""
            # Catching Exception is not ideal, but we don't care much for the error here, and just
            # default to setting a blank version since there isn't much the admin can do!
            except Exception as e:
                logger.warning("unable to get version from API: %s", str(e))
                logger.debug(e, exc_info=True)
                return ""
        return ""

    def _get_version(self) -> Optional[str]:
        """Helper for fetching the version from the running workload using the Tempo CLI."""

        container = self.unit.get_container('tempo')
        proc = container.exec(["/tempo", "-version"])
        out, err = proc.wait_output()

        # example output:
        # / # /tempo --version
        # tempo, version  (branch: HEAD, revision: fd5743d5d)
        #   build user:
        #   build date:
        #   go version:       go1.18.5
        #   platform:         linux/amd64

        if version_head := re.search(r"tempo, version (.*) \(branch: (.*), revision: (.*)\)", out):
            v_head, b_head, r_head = version_head.groups()
            version = f"{v_head}:{b_head}/{r_head}"
        elif version_headless := re.search(r"tempo, version (\S+)", out):
            version = version_headless.groups()[0]
        else:
            logger.warning(f'unable to determine tempo workload version: output {out} '
                           f'does not match any known pattern')
            return
        return version


if __name__ == "__main__":  # pragma: nocover
    main(TempoCharm)
