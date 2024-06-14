#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight object storage based tracing backend."""
import json
import logging
import re
import socket
from pathlib import Path
from typing import Dict, List
from typing import Optional, Set, Tuple

import ops
from ops.charm import (
    CharmBase,
    CollectStatusEvent,
    PebbleNoticeEvent,
    RelationEvent,
    WorkloadEvent,
)
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.model import Relation

from charms.data_platform_libs.v0.s3 import S3Requirer
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.loki_k8s.v0.loki_push_api import LogProxyConsumer
from charms.observability_libs.v0.kubernetes_service_patch import KubernetesServicePatch
from charms.observability_libs.v1.cert_handler import CertHandler, VAULT_SECRET_LABEL
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_k8s.v1.charm_tracing import trace_charm
from charms.tempo_k8s.v2.tracing import (
    ReceiverProtocol,
    RequestEvent,
    TracingEndpointProvider,
)
from charms.traefik_route_k8s.v0.traefik_route import TraefikRouteRequirer
from coordinator import TempoCoordinator
from tempo import Tempo
from tempo_cluster import TempoClusterProvider

logger = logging.getLogger(__name__)


@trace_charm(
    tracing_endpoint="tempo_otlp_http_endpoint",
    server_cert="server_cert",
    extra_types=(Tempo, TracingEndpointProvider),
)
class TempoCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)
        self.ingress = TraefikRouteRequirer(self, self.model.get_relation("ingress"), "ingress")  # type: ignore
        self.tempo_cluster = TempoClusterProvider(self)
        self.coordinator = TempoCoordinator(self.tempo_cluster,
                                            is_worker=self.is_worker_node)

        self.tempo = tempo = Tempo(
            self.unit.get_container("tempo"),
            external_host=self.hostname,
            # we need otlp_http receiver for charm_tracing
            enable_receivers=["otlp_http"],
            run_worker_node=self.is_worker_node
        )

        self.cert_handler = CertHandler(
            self,
            key="tempo-server-cert",
            sans=[self.hostname],
        )

        self.s3_requirer = S3Requirer(self, Tempo.s3_relation_name, Tempo.s3_bucket_name)

        # configure this tempo as a datasource in grafana
        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="tempo",
            source_url=self._external_http_server_url,
            refresh_event=[
                # refresh the source url when TLS config might be changing
                self.on[self.cert_handler.certificates_relation_name].relation_changed,
                # or when ingress changes
                self.ingress.on.ready,
            ],
        )
        # # Patch the juju-created Kubernetes service to contain the right ports
        external_ports = tempo.get_external_ports(self.app.name)
        self._service_patcher = KubernetesServicePatch(self, external_ports)
        # Provide ability for Tempo to be scraped by Prometheus using prometheus_scrape
        self._scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": [f"*:{tempo.tempo_http_server_port}"]}]}],
        )
        # Enable log forwarding for Loki and other charms that implement loki_push_api
        self._logging = LogProxyConsumer(
            self, relation_name="logging", log_files=[self.tempo.log_path], container_name="tempo"
        )
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        self.tracing = TracingEndpointProvider(self, external_url=self._external_url)
        self._inconsistencies = self.coordinator.get_deployment_inconsistencies(
            clustered=self.is_clustered,
            scaled=self.is_scaled,
            has_workers=self.tempo_cluster.has_workers,
            is_worker_node=self.is_worker_node,
            has_s3=self.is_s3_ready
        )
        self._is_consistent = not self._inconsistencies

        if not self._is_consistent:
            logger.error(
                f"Inconsistent deployment. {self.unit.name} will be shutting down. "
                "This likely means you need to add an s3 integration. "
                "This charm will be unresponsive and refuse to handle any event until "
                "the situation is resolved by the cloud admin, to avoid data loss."
            )
            self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

            if self.tempo.is_tempo_service_defined:
                self.tempo.shutdown()

            return  # refuse to handle any other event as we can't possibly know what to do.

        # lifecycle
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.leader_settings_changed, self._on_leader_settings_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.list_receivers_action, self._on_list_receivers_action)

        # ingress
        ingress = self.on["ingress"]
        self.framework.observe(ingress.relation_created, self._on_ingress_relation_created)
        self.framework.observe(ingress.relation_joined, self._on_ingress_relation_joined)
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)

        # workload
        self.framework.observe(self.on.tempo_pebble_ready, self._on_tempo_pebble_ready)
        self.framework.observe(
            self.on.tempo_pebble_custom_notice, self._on_tempo_pebble_custom_notice
        )

        # s3
        self.framework.observe(
            self.s3_requirer.on.credentials_changed, self._on_s3_credentials_changed
        )
        self.framework.observe(self.s3_requirer.on.credentials_gone, self._on_s3_credentials_gone)

        # tracing
        self.framework.observe(self.tracing.on.request, self._on_tracing_request)
        self.framework.observe(self.tracing.on.broken, self._on_tracing_broken)
        self.framework.observe(
            self.on.tempo_peers_relation_created, self._on_tempo_peers_relation_created
        )
        self.framework.observe(
            self.on.tempo_peers_relation_changed, self._on_tempo_peers_relation_changed
        )

        # tls
        self.framework.observe(self.cert_handler.on.cert_changed, self._on_cert_handler_changed)

        # cluster
        self.framework.observe(self.tempo_cluster.on.changed, self._on_tempo_cluster_changed)

    ######################
    # UTILITY PROPERTIES #
    ######################

    @property
    def is_worker_node(self) -> bool:
        """Check whether this Tempo charm is configured to run a worker node."""
        if self.is_clustered:
            return self.config.get("coordinator_runs_workload_when_clustered", True)
        return True

    @property
    def is_scaled(self) -> bool:
        """Check whether Tempo is deployed with scale > 1."""
        relation = self.model.get_relation("tempo-peers")
        if not relation:
            return False
        # does not include self
        return len(relation.units) > 0

    @property
    def is_clustered(self) -> bool:
        """Check whether this Tempo is a coordinator and has worker nodes connected to it."""
        return self.tempo_cluster.has_workers

    @property
    def is_s3_ready(self) -> bool:
        # we have an s3 config

        # we cannot check for self.tempo.can_scale() here, because if we are handling a s3-changed
        # event, it may be that we have a s3 config which is not yet on disk (it will be put there by
        # our on_s3_changed event handler momentarily).
        return bool(self._s3_config)

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _external_http_server_url(self) -> str:
        """External url of the http(s) server."""
        return f"{self._external_url}:{self.tempo.tempo_http_server_port}"

    @property
    def _external_url(self) -> str:
        """Return the external url."""
        if self.ingress.is_ready():
            ingress_url = f"{self.ingress.scheme}://{self.ingress.external_host}"
            logger.debug("This unit's ingress URL: %s", ingress_url)
            return ingress_url

        # If we do not have an ingress, then use the pod hostname.
        # The reason to prefer this over the pod name (which is the actual
        # hostname visible from the pod) or a K8s service, is that those
        # are routable virtually exclusively inside the cluster (as they rely)
        # on the cluster's DNS service, while the ip address is _sometimes_
        # routable from the outside, e.g., when deploying on MicroK8s on Linux.
        return self._internal_url

    @property
    def _internal_url(self) -> str:
        scheme = "https" if self.tls_available else "http"
        return f"{scheme}://{self.hostname}"

    @property
    def tls_available(self) -> bool:
        """Return True if tls is enabled and the necessary certs are found."""
        return (
                self.cert_handler.enabled
                and (self.cert_handler.server_cert is not None)
                and (self.cert_handler.private_key is not None)
                and (self.cert_handler.ca_cert is not None)
        )

    @property
    def _s3_config(self) -> Optional[dict]:
        if not self.s3_requirer.relations:
            return None
        s3_config = self.s3_requirer.get_s3_connection_info()
        if (
                s3_config
                and "bucket" in s3_config
                and "endpoint" in s3_config
                and "access-key" in s3_config
                and "secret-key" in s3_config
        ):
            return s3_config
        return None

    ##################
    # EVENT HANDLERS #
    ##################
    def _on_tracing_broken(self, _):
        """Update tracing relations' databags once one relation is removed."""
        self._update_tracing_relations()

    def _on_cert_handler_changed(self, _):
        was_ready = self.tempo.tls_ready

        if self.tls_available:
            logger.debug("enabling TLS")
            self.tempo.configure_tls(
                cert=self.cert_handler.server_cert,  # type: ignore
                key=self.cert_handler.private_key,  # type: ignore
                ca=self.cert_handler.ca_cert,  # type: ignore
            )
        else:
            logger.debug("disabling TLS")
            self.tempo.clear_tls_config()

        if was_ready != self.tempo.tls_ready:
            # tls readiness change means config change.
            self._update_tempo_config()
            # sync scheme change with traefik and related consumers
            self._configure_ingress()

            if self.tempo.is_tempo_service_defined:
                self.tempo.restart()

        # sync the server cert with the charm container.
        # technically, because of charm tracing, this will be called first thing on each event
        self._update_server_cert()

        # update relations to reflect the new certificate
        self._update_tracing_relations()

        # notify the cluster
        self._update_tempo_cluster()

    def _on_tracing_request(self, e: RequestEvent):
        """Handle a remote requesting a tracing endpoint."""
        logger.debug(f"received tracing request from {e.relation.app}: {e.requested_receivers}")
        self._update_tracing_relations()

    def _on_tempo_cluster_changed(self, _: RelationEvent):
        self._update_tempo_cluster()

    def _on_ingress_relation_created(self, _: RelationEvent):
        self._configure_ingress()

    def _on_ingress_relation_joined(self, _: RelationEvent):
        self._configure_ingress()

    def _on_leader_settings_changed(self, _: ops.LeaderSettingsChangedEvent):
        if not self.is_s3_ready:
            logger.error(
                "Losing leadership without s3. " "This unit will soon be in an inconsistent state."
            )

    def _on_leader_elected(self, _: ops.LeaderElectedEvent):
        # as traefik_route goes through app data, we need to take lead of traefik_route if our leader dies.
        self._configure_ingress()

    def _on_s3_credentials_changed(self, _):
        self._on_s3_changed()

    def _on_s3_credentials_gone(self, _):
        self._on_s3_changed()

    def _on_s3_changed(self):
        could_scale_before = self.tempo.can_scale()

        self._update_tempo_config()

        can_scale_now = self.tempo.can_scale()
        # if we had s3, and we don't anymore, we need to replan from 'scaling-monolithic' to 'all'
        # if we didn't have s3, and now we do, we can replan from 'all' to 'scaling-monolithic'
        if could_scale_before != can_scale_now:
            if not self.tempo.is_tempo_service_defined:
                # TODO: should we be deferring this to the next pebble-ready instead?
                logger.debug("tempo was not running! Starting it now")
            self.tempo.plan()

        self._update_tempo_cluster()

    def _on_tempo_peers_relation_created(self, event: ops.RelationCreatedEvent):
        if self._local_ip:
            event.relation.data[self.unit]["local-ip"] = self._local_ip

    def _on_tempo_peers_relation_changed(self, _):
        if self._update_tempo_config():
            self.tempo.restart()

    def _update_tempo_config(self) -> bool:
        peers = self.peers()
        relation = self.model.get_relation("tempo-peers")
        # get unit addresses for all the other units from a databag
        if peers and relation:
            addresses = [relation.data[unit].get("local-ip") for unit in peers]
            addresses = list(filter(None, addresses))
        else:
            addresses = []

        # add own address
        if self._local_ip:
            addresses.append(self._local_ip)

        return self.tempo.update_config(self._requested_receivers(), self._s3_config, addresses)

    @property
    def _local_ip(self) -> Optional[str]:
        try:
            return str(self.model.get_binding('tempo-peers').network.bind_address)
        except (ops.ModelError, KeyError) as e:
            logger.debug("failed to obtain local ip from tempo-peers binding", exc_info=True)
            logger.error(f"unable to get local IP at this time: failed with {type(e)}; "
                         f"see debug log for more info")
            return None

    def _on_config_changed(self, _):
        # check if certificate files haven't disappeared and recreate them if needed
        if self.tls_available and not self.tempo.tls_ready:
            logger.debug("enabling TLS")
            self.tempo.configure_tls(
                cert=self.cert_handler.server_cert,  # type: ignore
                key=self.cert_handler.private_key,  # type: ignore
                ca=self.cert_handler.ca_cert,  # type: ignore
            )

        self._update_tempo_cluster()

    def _on_tempo_pebble_custom_notice(self, event: PebbleNoticeEvent):
        if event.notice.key == self.tempo.tempo_ready_notice_key:
            logger.debug("pebble api reported ready")
            # collect-unit-status should do the rest and report that pebble is ready.
            self.tempo.receive_tempo_ready_notice()

    def _on_tempo_pebble_ready(self, event: WorkloadEvent):
        if not self.tempo.container.can_connect():
            logger.warning("container not ready, cannot configure; will retry soon")
            return event.defer()

        self.tempo.update_config(self._requested_receivers(), self._s3_config)
        self.tempo.plan()

        self.unit.set_workload_version(self.version)
        self.unit.status = ActiveStatus()

    def _on_update_status(self, _):
        """Update the status of the application."""
        self.unit.set_workload_version(self.version)

    def _on_ingress_ready(self, _event):
        # whenever there's a change in ingress, we need to update all tracing relations
        self._update_tracing_relations()

    def _on_ingress_revoked(self, _event):
        # whenever there's a change in ingress, we need to update all tracing relations
        self._update_tracing_relations()

    def _on_list_receivers_action(self, event: ops.ActionEvent):
        res = {}
        for receiver in self._requested_receivers():
            res[receiver.replace("_", "-")] = (
                f"{self.ingress.external_host or self.tempo.url}:{self.tempo.receiver_ports[receiver]}"
            )
        event.set_results(res)

    # keep this event handler at the bottom
    def _on_collect_unit_status(self, e: CollectStatusEvent):
        # todo add [nginx.workload] statuses

        if not self.tempo.container.can_connect():
            e.add_status(WaitingStatus("[workload.tempo] Tempo container not ready"))
        if not self.tempo.is_ready():
            e.add_status(WaitingStatus("[workload.tempo] Tempo API not ready just yet..."))

        # todo: how to surface this inconsistent state?
        # if not self.tempo.can_scale() and self.is_s3_ready):
        #     e.add_status(BlockedStatus("[s3] s3 ready but tempo not configured."))

        # TODO: should we set these statuses on the leader only, or on all units?
        if issues := self._inconsistencies:
            for issue in issues:
                e.add_status(
                    BlockedStatus("[consistency.issues]" + issue)
                )
            e.add_status(
                BlockedStatus(
                    "[consistency] Unit *disabled*."
                )
            )
        else:
            if self.is_clustered:
                # no issues: tempo is consistent
                if not self.coordinator.is_recommended:
                    e.add_status(ActiveStatus("[coordinator] degraded"))
                else:
                    e.add_status(ActiveStatus())
            else:
                e.add_status(ActiveStatus())

    ###################
    # UTILITY METHODS #
    ###################
    def _configure_ingress(self) -> None:
        """Make sure the traefik route and tracing relation data are up-to-date."""
        if not self.unit.is_leader():
            return

        if self.ingress.is_ready():
            self.ingress.submit_to_traefik(
                self._ingress_config, static=self._static_ingress_config
            )
            if self.ingress.external_host:
                self._update_tracing_relations()

    def _update_tracing_relations(self):
        tracing_relations = self.model.relations["tracing"]
        if not tracing_relations:
            # todo: set waiting status and configure tempo to run without receivers if possible,
            #  else perhaps postpone starting the workload at all.
            logger.warning("no tracing relations: Tempo has no receivers configured.")
            return

        requested_receivers = self._requested_receivers()
        # publish requested protocols to all relations
        if self.unit.is_leader():
            self.tracing.publish_receivers(
                [(p, self.tempo.get_receiver_url(p, self.ingress)) for p in requested_receivers]
            )

        self._restart_if_receivers_changed()

        self._update_tempo_cluster()

    def _restart_if_receivers_changed(self):
        # if the receivers have changed, we need to reconfigure tempo
        self.unit.status = MaintenanceStatus("reconfiguring Tempo...")
        updated = self._update_tempo_config()
        if not updated:
            logger.debug("Config not updated; skipping tempo restart")
        if updated:
            restarted = self.tempo.is_tempo_service_defined and self.tempo.restart()
            if not restarted:
                # assume that this will be handled at the next pebble-ready
                logger.debug("Cannot reconfigure/restart tempo at this time.")

    def _requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """List what receivers we should activate, based on the active tracing relations."""
        # we start with the sum of the requested endpoints from the requirers
        requested_protocols = set(self.tracing.requested_protocols())

        # and publish only those we support
        requested_receivers = requested_protocols.intersection(set(self.tempo.receiver_ports))
        requested_receivers.update(self.tempo.enabled_receivers)
        return tuple(requested_receivers)

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

    def server_cert(self):
        """For charm tracing."""
        self._update_server_cert()
        return self.tempo.server_cert_path

    def _update_server_cert(self):
        """Server certificate for charm tracing tls, if tls is enabled."""
        server_cert = Path(self.tempo.server_cert_path)
        if self.tls_available:
            if not server_cert.exists():
                server_cert.parent.mkdir(parents=True, exist_ok=True)
                if self.cert_handler.server_cert:
                    server_cert.write_text(self.cert_handler.server_cert)
        else:  # tls unavailable: delete local cert
            server_cert.unlink(missing_ok=True)

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        if self.tempo.is_ready():
            return f"{self._internal_url}:{self.tempo.receiver_ports['otlp_http']}"

        return None

    def peers(self) -> Optional[Set[ops.model.Unit]]:
        relation = self.model.get_relation("tempo-peers")
        if not relation:
            return None

        # self is not included in relation.units
        return relation.units

    def _is_s3_ready(self) -> bool:
        return bool(self._s3_config)

    @property
    def loki_endpoints_by_unit(self) -> Dict[str, str]:
        """Loki endpoints from relation data in the format needed for Pebble log forwarding.

        Returns:
            A dictionary of remote units and the respective Loki endpoint.
            {
                "loki/0": "http://loki:3100/loki/api/v1/push",
                "another-loki/0": "http://another-loki:3100/loki/api/v1/push",
            }
        """
        endpoints: Dict = {}
        relations: List[Relation] = self.model.relations.get("logging-consumer", [])

        for relation in relations:
            for unit in relation.units:
                if "endpoint" not in relation.data[unit]:
                    continue
                endpoint = relation.data[unit]["endpoint"]
                deserialized_endpoint = json.loads(endpoint)
                url = deserialized_endpoint["url"]
                endpoints[unit.name] = url

        return endpoints

    def _update_tempo_cluster(self):
        """Build the config and publish everything to the application databag."""
        if not self.coordinator.is_coherent:
            return

        kwargs = {}

        if self.tls_available:
            # we share the certs in plaintext as they're not sensitive information
            kwargs['ca_cert'] = self.cert_handler.ca_cert
            kwargs['server_cert'] = self.cert_handler.server_cert
            kwargs['privkey_secret_id'] = self.tempo_cluster.publish_privkey(VAULT_SECRET_LABEL)

        # On every function call, we always publish everything to the databag; however, if there
        # are no changes, Juju will notice there's no delta and do nothing
        self.tempo_cluster.publish_data(
            tempo_config=self.tempo.generate_config(self._requested_receivers(), self._s3_config),
            loki_endpoints=self.loki_endpoints_by_unit,
            # TODO tempo receiver for charm tracing
            **kwargs
        )

    @property
    def _static_ingress_config(self) -> dict:
        entry_points = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            entry_points[sanitized_protocol] = {"address": f":{port}"}

        return {"entryPoints": entry_points}

    @property
    def _ingress_config(self) -> dict:
        """Build a raw ingress configuration for Traefik."""
        http_routers = {}
        http_services = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            http_routers[f"juju-{self.model.name}-{self.model.app.name}-{sanitized_protocol}"] = {
                "entryPoints": [sanitized_protocol],
                "service": f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
            }
            if sanitized_protocol.endswith("grpc") and not self.tls_available:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": [{"url": f"h2c://{self.hostname}:{port}"}]}}
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": [{"url": f"{self._internal_url}:{port}"}]}}
        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
            },
        }


if __name__ == "__main__":  # pragma: nocover
    main(TempoCharm)
