#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""
import logging
import socket
import time
from subprocess import CalledProcessError, getoutput
from typing import Dict, List, Optional, Sequence, Tuple

import ops
import yaml
from charms.tempo_k8s.v2.tracing import ReceiverProtocol
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class Tempo:
    """Class representing the Tempo client workload configuration."""

    config_path = "/etc/tempo/tempo.yaml"
    wal_path = "/etc/tempo/tempo_wal"
    log_path = "/var/log/tempo.log"
    tempo_ready_notice_key = "canonical.com/tempo/workload-ready"

    server_ports = {
        "tempo_http": 3200,
        # "tempo_grpc": 9096, # default grpc listen port is 9095, but that conflicts with promtail.
    }

    receiver_ports: Dict[ReceiverProtocol, int] = {
        "zipkin": 9411,
        "otlp_grpc": 4317,
        "otlp_http": 4318,
        "jaeger_thrift_http": 14268,
        # todo if necessary add support for:
        #  "kafka": 42,
        #  "jaeger_grpc": 14250,
        #  "opencensus": 43,
        #  "jaeger_thrift_compact": 44,
        #  "jaeger_thrift_binary": 45,
    }

    all_ports = {**server_ports, **receiver_ports}

    def __init__(
        self,
        container: ops.Container,
        local_host: str = "0.0.0.0",
        enable_receivers: Optional[Sequence[ReceiverProtocol]] = None,
    ):
        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml
        self._local_hostname = local_host
        self.container = container
        self.enabled_receivers = enable_receivers or []

    @property
    def tempo_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_http"]

    def get_external_ports(self, service_name_prefix: str) -> List[Tuple[str, int, int]]:
        """List of service names and port mappings for the kubernetes service patch.

        Includes the tempo server as well as the receiver ports.
        """
        # todo allow remapping ports?
        all_ports = {**self.server_ports}
        return [
            (
                (f"{service_name_prefix}-{service_name}").replace("_", "-"),
                all_ports[service_name],
                all_ports[service_name],
            )
            for service_name in all_ports
        ]

    @property
    def host(self) -> str:
        """Hostname at which tempo is running."""
        return socket.getfqdn()

    @property
    def url(self) -> str:
        """Base url at which the tempo server is locally reachable over http."""
        return f"http://{self.host}"

    def plan(self):
        """Update pebble plan and start the tempo-ready service."""
        self.container.add_layer("tempo", self.pebble_layer, combine=True)
        self.container.add_layer("tempo-ready", self.tempo_ready_layer, combine=True)
        self.container.replan()

        # is not autostart-enabled, we just run it once on pebble-ready.
        self.container.start("tempo-ready")

    def update_config(self, requested_receivers: Sequence[ReceiverProtocol]) -> bool:
        """Generate a config and push it to the container it if necessary."""
        container = self.container
        if not container.can_connect():
            logger.debug("Container can't connect: config update skipped.")
            return False

        new_config = self.generate_config(requested_receivers)

        if self.get_current_config() != new_config:
            logger.debug("Pushing new config to container...")
            container.push(
                self.config_path,
                yaml.safe_dump(new_config),
                make_dirs=True,
            )
            return True
        return False

    def restart(self) -> bool:
        """Try to restart the tempo service."""
        if not self.container.can_connect():
            return False
        retry_count = 0
        while retry_count < 5:
            try:
                self.container.stop("tempo")
                service_status = self.container.get_service("tempo").current
                # verify if tempo is already inactive, then try to start a new instance
                if service_status == ops.pebble.ServiceStatus.INACTIVE:
                    self.container.start("tempo")
                    return True
                else:
                    # old tempo is still active / errored out
                    retry_count += 1
                    logger.warning(f"tempo container is in status {service_status}, trying to start again in {retry_count * 2}s. retry {retry_count}")
            except ops.pebble.Error:
                # old tempo might not be taken down yet
                retry_count += 1
                logger.exception(f"Pebble error on restarting Tempo. Retrying in {retry_count * 2}s")
            time.sleep(2 * retry_count)
        return False

    def get_current_config(self) -> Optional[dict]:
        """Fetch the current configuration from the container."""
        if not self.container.can_connect():
            return None
        try:
            return yaml.safe_load(self.container.pull(self.config_path))
        except ops.pebble.PathError:
            return None

    def generate_config(self, receivers: Sequence[ReceiverProtocol]) -> dict:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        return {
            "auth_enabled": False,
            "server": {
                "http_listen_port": self.tempo_server_port,
                # "grpc_listen_port": self.receiver_ports["tempo_grpc"],
            },
            # more configuration information can be found at
            # https://github.com/open-telemetry/opentelemetry-collector/tree/overlord/receiver
            "distributor": {"receivers": self._build_receivers_config(receivers)},
            # the length of time after a trace has not received spans to consider it complete and flush it
            # cut the head block when it hits this number of traces or ...
            #   this much time passes
            "ingester": {
                "trace_idle_period": "10s",
                "max_block_bytes": 100,
                "max_block_duration": "30m",
            },
            "compactor": {
                "compaction": {
                    # blocks in this time window will be compacted together
                    "compaction_window": "1h",
                    # maximum size of compacted blocks
                    "max_compaction_objects": 1000000,
                    "block_retention": "1h",
                    "compacted_block_retention": "10m",
                    "v2_out_buffer_bytes": 5242880,
                }
            },
            # see https://grafana.com/docs/tempo/latest/configuration/#storage
            "storage": {
                "trace": {
                    # FIXME: not good for production! backend configuration to use;
                    #  one of "gcs", "s3", "azure" or "local"
                    "backend": "local",
                    "local": {"path": "/traces"},
                    "wal": {
                        # where to store the wal locally
                        "path": self.wal_path
                    },
                    "pool": {
                        # number of traces per index record
                        "max_workers": 400,
                        "queue_depth": 20000,
                    },
                }
            },
        }

    @property
    def pebble_layer(self) -> Layer:
        """Generate the pebble layer for the Tempo container."""
        return Layer(
            {
                "services": {
                    "tempo": {
                        "override": "replace",
                        "summary": "Main Tempo layer",
                        "command": f'/bin/sh -c "/tempo -config.file={self.config_path} | tee {self.log_path}"',
                        "startup": "enabled",
                    }
                },
            }
        )

    @property
    def tempo_ready_layer(self) -> Layer:
        """Generate the pebble layer to fire the tempo-ready custom notice."""
        return Layer(
            {
                "services": {
                    "tempo-ready": {
                        "override": "replace",
                        "summary": "Notify charm when tempo is ready",
                        "command": f"""watch -n 5 '[ $(wget -q -O- localhost:{self.tempo_server_port}/ready) = "ready" ] && 
                                   ( /charm/bin/pebble notify {self.tempo_ready_notice_key} ) || 
                                   ( echo "tempo not ready" )'""",
                        "startup": "disabled",
                    }
                },
            }
        )

    def is_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        try:
            out = getoutput(
                f"curl http://{self._local_hostname}:{self.tempo_server_port}/ready"
            ).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    def _build_receivers_config(self, receivers: Sequence[ReceiverProtocol]):  # noqa: C901
        # receivers: the receivers we have to enable because the requirers we're related to
        # intend to use them
        # it already includes self.enabled_receivers: receivers we have to enable because *this charm* will use them.
        receivers_set = set(receivers)

        if not receivers_set:
            logger.warning("No receivers set. Tempo will be up but not functional.")

        config = {}

        # TODO: how do we pass the ports into this config?
        if "zipkin" in receivers_set:
            config["zipkin"] = None
        if "opencensus" in receivers_set:
            config["opencensus"] = None

        otlp_config = {}
        if "otlp_http" in receivers_set:
            otlp_config["http"] = None
        if "otlp_grpc" in receivers_set:
            otlp_config["grpc"] = None
        if otlp_config:
            config["otlp"] = {"protocols": otlp_config}

        jaeger_config = {}
        if "jaeger_thrift_http" in receivers_set:
            jaeger_config["thrift_http"] = None
        if "jaeger_grpc" in receivers_set:
            jaeger_config["grpc"] = None
        if "jaeger_thrift_binary" in receivers_set:
            jaeger_config["thrift_binary"] = None
        if "jaeger_thrift_compact" in receivers_set:
            jaeger_config["thrift_compact"] = None
        if jaeger_config:
            config["jaeger"] = {"protocols": jaeger_config}

        return config
