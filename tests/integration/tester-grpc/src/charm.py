#!/usr/bin/env python3
# Copyright 2024 Canonical Inc.
# See LICENSE file for licensing details.

import logging
import time
from pathlib import Path
from typing import List, Optional

from charms.tempo_k8s.v2.tracing import TracingEndpointRequirer
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from ops.charm import CharmBase, PebbleReadyEvent
from ops.main import main
from ops.model import (
    ActiveStatus,
    Container,
    MaintenanceStatus,
    Relation,
    WaitingStatus,
)
from ops.pebble import Layer

logger = logging.getLogger(__name__)
TRACING_APP_NAME = "TempoTesterGrpcCharm"


class TempoTesterGrpcCharm(CharmBase):
    """Charm the service."""

    _layer_name = _service_name = "tester"
    _container_name = "workload"
    _peer_relation_name = "replicas"
    _address_name = "private-address-ip"

    def __init__(self, *args):
        super().__init__(*args)

        self.container: Container = self.unit.get_container(self._container_name)

        self.tracing = TracingEndpointRequirer(
            self, relation_name="tracing", protocols=["otlp_grpc"]
        )

        # Core lifecycle events
        self.framework.observe(self.on.config_changed, self._update)

        # Peer relation events
        self.framework.observe(self.on[self._peer_relation_name].relation_joined, self._update)
        self.framework.observe(self.on[self._peer_relation_name].relation_changed, self._update)
        self.framework.observe(self.tracing.on.endpoint_changed, self._update)

    @property
    def peers(self) -> List[str]:
        return self._get_peer_addresses()

    def _on_tester_pebble_ready(self, e: PebbleReadyEvent):
        if e.workload.can_connect():
            self._setup_container(e.workload)
        else:
            self.unit.status = WaitingStatus("waiting for container connectivity...")
            return e.defer()
        self.unit.status = ActiveStatus("ready")

    def ensure_container_setup(self):
        container = self.container
        if container.list_files("/", pattern="webserver.py"):
            return
        self._setup_container(container)

    def _setup_container(self, container: Container):
        # copy the webserver file to the container. In a production environment,
        # the workload would typically be an OCI image. Here however we have a
        # 'bare' python container as base.
        self.unit.status = MaintenanceStatus("copying over webserver source")

        resources = Path(__file__).parent / "resources"
        webserver_source_path = resources / "webserver.py"
        logger.info(webserver_source_path)
        with open(webserver_source_path, "r") as webserver_source:
            logger.info("pushing webserver source...")
            container.push("/webserver.py", webserver_source)

        self.unit.status = MaintenanceStatus("installing software in workload container")
        # we install the webserver dependencies; in a production environment, these
        # would typically be baked in the workload OCI image.
        webserver_dependencies_path = resources / "webserver-dependencies.txt"
        logger.info(webserver_dependencies_path)
        with open(webserver_dependencies_path, "r") as dependencies_file:
            dependencies = dependencies_file.read().split("\n")
            logger.info(f"installing webserver dependencies {dependencies}...")
            container.exec(["pip", "install", *dependencies]).wait()

        self.unit.status = MaintenanceStatus("container ready")

    # Actual tester stuff
    def _tester_layer(self):
        """Returns a Pebble configuration layer for the tester runtime."""
        env = {
            "PORT": self.config["port"],
            "HOST": self.config["host"],
            "APP_NAME": self.app.name,
            "TEMPO_ENDPOINT": str(self.tracing.get_endpoint("otlp_grpc") or ""),
        }
        logging.info(f"Initing pebble layer with env: {str(env)}")

        if self.unit.name.split("/")[1] == "0":
            env["PEERS"] = peers = ";".join(self.peers)
            env["OVERLORD"] = "1"
            logging.info(f"Configuring overlord node; Peers: {peers}")

        return Layer(
            {
                "summary": "tester layer",
                "description": "pebble config layer for tester",
                "services": {
                    "tester": {
                        "override": "merge",
                        "summary": "tester service",
                        "command": "python webserver.py > webserver.log",
                        "startup": "enabled",
                        "environment": env,
                    }
                },
            }
        )

    # source: https://github.com/canonical/alertmanager-k8s-operator
    def _restart_service(self) -> bool:
        """Helper function for restarting the underlying service.
        Returns:
            True if restart succeeded; False otherwise.
        """
        logger.info("Restarting service %s", self._service_name)

        if not self.container.can_connect():
            logger.error("Cannot (re)start service: container is not ready.")
            return False

        # Check if service exists, to avoid ModelError from being raised when the service does
        # not exist,
        if not self.container.get_plan().services.get(self._service_name):
            logger.error("Cannot (re)start service: service does not (yet) exist.")
            return False

        logger.info(f"pebble env, {self.container.get_plan().services.get('tester').environment}")

        self.container.restart(self._service_name)
        logger.info(f"restarted {self._service_name}")
        return True

    def _update_layer(self, restart: bool) -> bool:
        """Update service layer to reflect changes in peers (replicas).
        Args:
          restart: a flag indicating if the service should be restarted if a change was detected.
        Returns:
          True if anything changed; False otherwise
        """
        overlay = self._tester_layer()
        plan = self.container.get_plan()

        logger.info("updating layer")

        if self._service_name not in plan.services or overlay.services != plan.services:
            logger.info("container.add_layer")
            self.container.add_layer(self._layer_name, overlay, combine=True)

            service_exists = self.container.get_plan().services.get("tester", None) is not None
            if service_exists and restart:
                self._restart_service()

            return True

        return False

    @property
    def peer_relation(self) -> Relation:
        """Helper function for obtaining the peer relation object.
        Returns: peer relation object
        (NOTE: would return None if called too early, e.g. during install).
        """
        return self.model.get_relation(self._peer_relation_name)

    @property
    def private_address(self) -> Optional[str]:
        """Get the unit's ip address.
        Technically, receiving a "joined" event guarantees an IP address is available. If this is
        called beforehand, a None would be returned.
        When operating a single unit, no "joined" events are visible so obtaining an address is a
        matter of timing in that case.
        This function is still needed in Juju 2.9.5 because the "private-address" field in the
        data bag is being populated by the app IP instead of the unit IP.
        Also in Juju 2.9.5, ip address may be None even after RelationJoinedEvent, for which
        "ops.model.RelationDataError: relation data values must be strings" would be emitted.
        Returns:
          None if no IP is available (called before unit "joined"); unit's ip address otherwise
        """

        # if bind_address := check_output(["unit-get", "private-address"]).decode().strip()
        if bind_address := self.model.get_binding(self._peer_relation_name).network.bind_address:
            bind_address = str(bind_address)
        return bind_address

    def update_address_in_relation_data(self, relation):
        """stores this unit's private IP in the relation databag"""
        relation.data[self.unit].update({self._address_name: self.private_address})
        logger.info(f"stored {self.private_address} in relation databag")

    def _update(self, _):
        """Event handler for all things."""
        self.ensure_container_setup()

        logger.info("running _update")
        if not self.container.can_connect():
            self.unit.status = WaitingStatus("waiting for container connectivity...")
            return

        # Wait for IP address. IP address is needed for forming tester clusters
        # and for related apps' config.
        if not self.private_address:
            self.unit.status = MaintenanceStatus("waiting for IP address...")
            return

        if not self.tracing.is_ready():
            self.unit.status = WaitingStatus("waiting for tracing to be ready...")
            return

        # In the case of a single unit deployment, no 'RelationJoined' event is emitted, so
        # setting IP here.
        # Store private address in unit's peer relation data bucket. This is still needed because
        # the "private-address" field in the data bag is being populated incorrectly.
        # Also, ip address may still be None even after RelationJoinedEvent, for which
        # "ops.model.RelationDataError: relation data values must be strings" would be emitted.
        if peer_relation := self.peer_relation:
            self.update_address_in_relation_data(peer_relation)
        else:
            logger.info("no peer relation to configure")

        self._update_layer(restart=True)
        self.unit.status = ActiveStatus("ready")

        self._send_grpc_traces()

    def _get_peer_addresses(self) -> List[str]:
        """Create a list of addresses of all peer units (all units excluding current).
        The returned addresses include the port number but do not include scheme (http).
        If a unit does not have an address, it will be omitted from the list.
        """
        addresses = []
        if pr := self.peer_relation:
            addresses = [
                f"{address}"
                for unit in pr.units
                # pr.units only holds peers (self.unit is not included)
                if (address := pr.data[unit].get(self._address_name))
            ]

        return addresses

    def tempo_otlp_grpc_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        if self.tracing.is_ready():
            return self.tracing.get_endpoint("otlp_grpc")
        else:
            return None

    @staticmethod
    def _emit_trace(endpoint: str, log_trace_to_console: bool = False):
        logger.info(f"Emitting traces to endpoint {endpoint}")
        span_exporter = OTLPSpanExporter(
            endpoint=endpoint,
            insecure=True,
        )
        resource = Resource.create(attributes={"service.name": TRACING_APP_NAME})
        provider = TracerProvider(resource=resource)
        if log_trace_to_console:
            processor = BatchSpanProcessor(ConsoleSpanExporter())
            provider.add_span_processor(processor)
        span_processor = BatchSpanProcessor(span_exporter)
        provider.add_span_processor(span_processor)
        trace.set_tracer_provider(provider)

        tracer = trace.get_tracer(__name__)
        logger.info(f"tracer instance: {tracer}")

        with tracer.start_as_current_span("foo"):
            with tracer.start_as_current_span("bar"):
                with tracer.start_as_current_span("baz"):
                    time.sleep(0.1)
                    logger.info("spans emitted")

    def _send_grpc_traces(self):
        if self.tracing.is_ready():
            logger.info("tracing is ready")
            endpoint = self.tempo_otlp_grpc_endpoint()
            self._emit_trace(endpoint)


if __name__ == "__main__":
    main(TempoTesterGrpcCharm)
