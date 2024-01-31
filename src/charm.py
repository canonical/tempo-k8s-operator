#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight elasticsearch alternative."""

import logging
import re
from typing import Optional, Tuple

from ops.charm import CharmBase, WorkloadEvent, RelationChangedEvent
from ops.main import main
from ops.model import ActiveStatus

import charms.tempo_k8s.v1.tracing as tracing_v1
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_k8s.v1.charm_tracing import trace_charm
from charms.tempo_k8s.v2.tracing import (RequestEvent, ReceiverProtocol, TracingEndpointProvider,
                                         )
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from tempo import Tempo

logger = logging.getLogger(__name__)

LEGACY_RECEIVER_PROTOCOLS = (
    "tempo",  # has since been renamed to "tempo_http"
    "otlp_grpc", "otlp_http", "zipkin",
    "jaeger_http_thrift",  # has since been renamed to "jaeger_thrift_http"
    "jaeger_grpc")
"""Receiver protocol names supported by tracing v0/v1."""


@trace_charm(
    tracing_endpoint="tempo_otlp_http_endpoint",
    extra_types=(Tempo, TracingEndpointProvider, tracing_v1.TracingEndpointProvider),
)
class TempoCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)
        tempo_pebble_ready_event = self.on.tempo_pebble_ready  # type:ignore
        self.framework.observe(tempo_pebble_ready_event, self._on_tempo_pebble_ready)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.tempo = tempo = Tempo()

        # configure this tempo as a datasource in grafana
        self.grafana_source_provider = GrafanaSourceProvider(
            self, source_type="tempo", source_port=str(tempo.tempo_port)
        )

        # # Patch the juju-created Kubernetes service to contain the right ports
        self._service_patcher = KubernetesServicePatch(
            self, tempo.get_requested_ports(self.app.name)
        )
        # Provide ability for Tempo to be scraped by Prometheus using prometheus_scrape
        self._scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": [f"*:{tempo.tempo_port}"]}]}],
        )

        # Enable log forwarding for Loki and other charms that implement loki_push_api
        self._logging = LogProxyConsumer(
            self, relation_name="logging", log_files=[self.tempo.log_path]
        )

        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        # Enable profiling over a relation with Parca
        # self._profiling = ProfilingEndpointProvider(
        #     self, jobs=[{"static_configs": [{"targets": ["*:4080"]}]}]
        # )

        self._tracing = TracingEndpointProvider(self, host=tempo.host)
        self.framework.observe(self._tracing.on.request, self._on_tracing_request)
        self.framework.observe(self.on.tracing_relation_changed, self._on_tracing_relation_changed)

        self._ingress = IngressPerAppRequirer(self, port=self.tempo.tempo_port)

    @property
    def legacy_v1_relations(self):
        tracing_relations = self.model.relations["tracing"]
        v1_relations = []
        for relation in tracing_relations:
            if not self._tracing.is_v2(relation):
                v1_relations.append(relation)
        return v1_relations

    def _on_tracing_request(self, e: RequestEvent):
        """Handle a remote requesting a tracing endpoint."""
        logger.debug(f"received tracing request from {e.relation.app}: {e.requested_receivers}")
        self._update_tracing_relations()

    def _on_tracing_relation_changed(self, e: RelationChangedEvent):
        # we have a relation-changed event; it might be caught by the v2 requirer
        # wrapper and turned into a `request` event, but also maybe not.
        relation = e.relation

        if self._tracing.is_v2(relation):
            # we will handle this as self._tracing.on.request on a different path
            return

        logger.debug(f"updating legacy v1 relation {relation}")
        # in v1, 'receiver' was called 'ingester'.
        receivers = [tracing_v1.Ingester(protocol=p, port=self.tempo.receiver_ports[p]) for p in
                     LEGACY_RECEIVER_PROTOCOLS]
        tracing_v1.TracingProviderAppData(
            host=self.tempo.host,
            ingesters=receivers
        ).dump(relation.data[self.app])

        # if this is the first legacy relation we get, we need to update ALL other relations
        # as we might need to add all legacy protocols to the mix
        self._update_tracing_relations()

    def _update_tracing_relations(self):
        tracing_relations = self.model.relations["tracing"]
        if not tracing_relations:
            # todo: set waiting status and configure tempo to run without receivers if possible,
            #  else perhaps postpone starting the workload at all.
            logger.warning("no tracing relations: Tempo has no receivers configured.")
            return

        requested_receivers = self._requested_receivers()
        # publish requested protocols to all v2 relations
        self._tracing.publish_receivers(
            ((p, self.tempo.receiver_ports[p]) for p in requested_receivers)
        )

    def _requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """What receivers we should activate, based on the active tracing relations."""

        # we start with the sum of the requested endpoints from the v2 requirers
        requested_protocols = set(self._tracing.requested_protocols())

        # if we have any v0/v1 requirer, we'll need to activate all supported legacy endpoints
        # and publish them too (only to v1 requirers).
        if self.legacy_v1_relations:
            requested_protocols.update(LEGACY_RECEIVER_PROTOCOLS)

        # and publish only those we support
        requested_receivers = requested_protocols.intersection(set(self.tempo.receivers))
        return tuple(requested_receivers)

    def _on_tempo_pebble_ready(self, event: WorkloadEvent):
        container = event.workload

        if not container.can_connect():
            return event.defer()

        # drop tempo_config.yaml into the container
        container.push(self.tempo.config_path, self.tempo.get_config(
            self._requested_receivers()
        ))

        container.add_layer("tempo", self.tempo.pebble_layer, combine=True)
        container.replan()

        self.unit.set_workload_version(self.version)
        self.unit.status = ActiveStatus()

    def _on_update_status(self, _):
        """Update the status of the application."""
        self.unit.set_workload_version(self.version)

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
        """Fetch the version from the running workload using the Tempo CLI.

        Helper function.
        """
        container = self.unit.get_container("tempo")
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
            logger.warning(
                f"unable to determine tempo workload version: output {out} "
                f"does not match any known pattern"
            )
            return
        return version

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        # TODO switch to HTTPS once SSL support is added
        return f"http://localhost:{self.tempo.otlp_http_port}/v1/traces"


if __name__ == "__main__":  # pragma: nocover
    main(TempoCharm)
