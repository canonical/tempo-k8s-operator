#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""
import logging
import socket
from pathlib import Path
from subprocess import CalledProcessError, getoutput
from typing import Dict, List, Optional, Sequence, Tuple

import ops
import tenacity
import yaml
from charms.tempo_k8s.v2.tracing import ReceiverProtocol, ReceiverProtocolType
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class Tempo:
    """Class representing the Tempo client workload configuration."""

    config_path = "/etc/tempo/tempo.yaml"

    # cert path on charm container
    server_cert_path = "/usr/local/share/ca-certificates/ca.crt"

    # cert paths on tempo container
    tls_cert_path = "/etc/tempo/tls/server.crt"
    tls_key_path = "/etc/tempo/tls/server.key"
    tls_ca_path = "/usr/local/share/ca-certificates/ca.crt"

    _tls_min_version = ""
    # cfr https://grafana.com/docs/enterprise-traces/latest/configure/reference/#supported-contents-and-default-values
    # "VersionTLS12"

    wal_path = "/etc/tempo/tempo_wal"
    log_path = "/var/log/tempo.log"
    tempo_ready_notice_key = "canonical.com/tempo/workload-ready"

    server_ports = {
        "tempo_http": 3200,
        "tempo_grpc": 9096,  # default grpc listen port is 9095, but that conflicts with promtail.
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
        charm: Optional[object] = None,
        external_host: Optional[str] = None,
        enable_receivers: Optional[Sequence[ReceiverProtocol]] = None,
    ):
        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml

        # fqdn, if an ingress is not available, else the ingress address.
        self._external_hostname = external_host or socket.getfqdn()
        self.container = container
        self.enabled_receivers = enable_receivers or []
        self.charm = charm

    @property
    def tempo_http_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_http"]

    @property
    def tempo_grpc_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_grpc"]

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
    def url(self) -> str:
        """Base url at which the tempo server is locally reachable over http."""
        scheme = "https" if self.tls_ready else "http"
        return f"{scheme}://{self._external_hostname}"

    def get_receiver_url(self, protocol: ReceiverProtocol):
        """Return the receiver endpoint URL based on the protocol."""
        protocol_type = ReceiverProtocolType.get(protocol)
        use_ingress = self.charm.ingress.is_ready()
        receiver_port = self.receiver_ports[protocol]

        if protocol_type == "http":
            if use_ingress:
                return f"{self.charm.ingress.scheme}://{self.charm.ingress.external_host}:{receiver_port}"
            return f"{self.url}:{receiver_port}"

        if use_ingress:
            return f"{self.charm.ingress.external_host}:{receiver_port}"
        return f"{self._external_hostname}:{receiver_port}"

    def get_ingressed_receiver_url(
        self,
        protocol: ReceiverProtocol,
    ):
        """Return the receiver endpoint URL based on the protocol."""
        protocol_type = ReceiverProtocolType.get(protocol)
        if protocol_type == "http":
            return f"{self.url}:{self.receiver_ports[protocol]}"
        return f"{self._external_hostname}:{self.receiver_ports[protocol]}"

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

    @tenacity.retry(
        # if restart FAILS (this function returns False)
        retry=tenacity.retry_if_result(lambda r: r is False),
        # we wait 3, 9, 27... up to 40 seconds between tries
        wait=tenacity.wait_exponential(multiplier=3, min=1, max=40),
        # we give up after 20 attempts
        stop=tenacity.stop_after_attempt(20),
        # if there's any exceptions throughout, raise them
        reraise=True,
    )
    def restart(self) -> bool:
        """Try to restart the tempo service."""
        # restarting tempo can cause errors such as:
        #  Could not bind to :3200 - Address in use
        # probably because of some lag with releasing the port. We restart tempo 'too quickly'
        # and it fails to start. As a workaround, see the @retry logic above.

        if not self.container.can_connect():
            return False

        self.container.stop("tempo")
        service_status = self.container.get_service("tempo").current

        # verify if tempo is already inactive, then try to start a new instance
        if service_status == ops.pebble.ServiceStatus.INACTIVE:
            try:
                self.container.start("tempo")
            except ops.pebble.ChangeError:
                # if tempo fails to start, we'll try again after retry backoff
                return False

            # set the notice to start checking for tempo server readiness so we don't have to
            # wait for an update-status
            self.container.start("tempo-ready")
            return True
        return False

    def get_current_config(self) -> Optional[dict]:
        """Fetch the current configuration from the container."""
        if not self.container.can_connect():
            return None
        try:
            return yaml.safe_load(self.container.pull(self.config_path))
        except ops.pebble.PathError:
            return None

    def configure_tls(self, *, cert: str, key: str, ca: str):
        """Push cert, key and CA to the tempo container."""
        # we save the cacert in the charm container too (for notices)
        Path(self.server_cert_path).write_text(ca)

        self.container.push(self.tls_cert_path, cert, make_dirs=True)
        self.container.push(self.tls_key_path, key, make_dirs=True)
        self.container.push(self.tls_ca_path, ca, make_dirs=True)
        self.container.exec(["update-ca-certificates"])

    def clear_tls_config(self):
        """Remove cert, key and CA files from the tempo container."""
        self.container.remove_path(self.tls_cert_path, recursive=True)
        self.container.remove_path(self.tls_key_path, recursive=True)
        self.container.remove_path(self.tls_ca_path, recursive=True)

    @property
    def tls_ready(self) -> bool:
        """Whether cert, key, and ca paths are found on disk and Tempo is ready to use tls."""
        if not self.container.can_connect():
            return False
        return all(
            self.container.exists(tls_path)
            for tls_path in (self.tls_cert_path, self.tls_key_path, self.tls_ca_path)
        )

    def _build_server_config(self):
        server_config = {
            "http_listen_port": self.tempo_http_server_port,
            # we need to specify a grpc server port even if we're not using the grpc server,
            # otherwise it will default to 9595 and make promtail bork
            "grpc_listen_port": self.tempo_grpc_server_port,
        }
        if self.tls_ready:
            for cfg in ("http_tls_config", "grpc_tls_config"):
                server_config[cfg] = {  # type: ignore
                    "cert_file": str(self.tls_cert_path),
                    "key_file": str(self.tls_key_path),
                    "client_ca_file": str(self.tls_ca_path),
                    "client_auth_type": "VerifyClientCertIfGiven",
                }
            server_config["tls_min_version"] = self._tls_min_version  # type: ignore

        return server_config

    def generate_config(self, receivers: Sequence[ReceiverProtocol]) -> dict:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        config = {
            "auth_enabled": False,
            "server": self._build_server_config(),
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
                    # total trace retention
                    "block_retention": "720h",
                    "compacted_block_retention": "1h",
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

        if self.tls_ready:
            # cfr:
            # https://grafana.com/docs/tempo/latest/configuration/network/tls/#client-configuration
            tls_config = {
                "tls_enabled": True,
                "tls_cert_path": self.tls_cert_path,
                "tls_key_path": self.tls_key_path,
                "tls_ca_path": self.tls_ca_path,
                # try with fqdn?
                "tls_server_name": self._external_hostname,
            }
            config["ingester_client"] = {"grpc_client_config": tls_config}
            config["metrics_generator_client"] = {"grpc_client_config": tls_config}

            # docs say it's `querier.query-frontend` but tempo complains about that
            config["querier"] = {"frontend_worker": {"grpc_client_config": tls_config}}

            # this is not an error.
            config["memberlist"] = tls_config

        return config

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
        s = "s" if self.tls_ready else ""
        return Layer(
            {
                "services": {
                    "tempo-ready": {
                        "override": "replace",
                        "summary": "Notify charm when tempo is ready",
                        "command": f"""watch -n 5 '[ $(wget -q -O- --no-check-certificate http{s}://localhost:{self.tempo_http_server_port}/ready) = "ready" ] &&
                                   ( /charm/bin/pebble notify {self.tempo_ready_notice_key} ) ||
                                   ( echo "tempo not ready" )'""",
                        "startup": "disabled",
                    }
                },
            }
        )

    def is_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        if self.tls_ready:
            tls, s = f" --cacert {self.server_cert_path}", "s"
        else:
            tls = s = ""

        # cert is for fqdn/ingress, not for IP
        cmd = f"curl{tls} http{s}://{self._external_hostname}:{self.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
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

        if self.tls_ready:
            receiver_config = {
                "tls": {
                    "ca_file": str(self.tls_ca_path),
                    "cert_file": str(self.tls_cert_path),
                    "key_file": str(self.tls_key_path),
                    "min_version": self._tls_min_version,
                }
            }
        else:
            receiver_config = None

        config = {}

        if "zipkin" in receivers_set:
            config["zipkin"] = receiver_config
        if "opencensus" in receivers_set:
            config["opencensus"] = receiver_config

        otlp_config = {}
        if "otlp_http" in receivers_set:
            otlp_config["http"] = receiver_config
        if "otlp_grpc" in receivers_set:
            otlp_config["grpc"] = receiver_config
        if otlp_config:
            config["otlp"] = {"protocols": otlp_config}

        jaeger_config = {}
        if "jaeger_thrift_http" in receivers_set:
            jaeger_config["thrift_http"] = receiver_config
        if "jaeger_grpc" in receivers_set:
            jaeger_config["grpc"] = receiver_config
        if "jaeger_thrift_binary" in receivers_set:
            jaeger_config["thrift_binary"] = receiver_config
        if "jaeger_thrift_compact" in receivers_set:
            jaeger_config["thrift_compact"] = receiver_config
        if jaeger_config:
            config["jaeger"] = {"protocols": jaeger_config}

        return config
