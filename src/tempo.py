#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""

import socket
from subprocess import CalledProcessError, getoutput
from typing import Dict, List, Sequence, Tuple

import yaml
from charms.tempo_k8s.v2.tracing import ReceiverProtocol
from ops.pebble import Layer


class Tempo:
    """Class representing the Tempo client workload configuration."""

    config_path = "/etc/tempo.yaml"
    wal_path = "/etc/tempo_wal"
    log_path = "/var/log/tempo.log"

    # todo make configurable?
    receiver_ports: Dict[ReceiverProtocol, int] = {
        "zipkin": 9411,
        "kafka": 42,  # TODO:
        "opencensus": 42,  # TODO:
        "tempo_http": 0,  # configurable; populated by __init__
        "tempo_grpc": 0,  # configurable; populated by __init__
        "tempo": 3200,  # legacy, renamed to tempo_http
        "otlp_grpc": 4317,
        "otlp_http": 4318,
        "jaeger_grpc": 14250,
        "jaeger_thrift_compact": 42,  # TODO:
        "jaeger_thrift_http": 14268,
        "jaeger_http_thrift": 14268,  # legacy, renamed to jaeger_thrift_http
        "jaeger_thrift_binary": 42,  # TODO:
    }

    def __init__(self, http_port: int = 3200, grpc_port: int = 9096, local_host: str = "0.0.0.0"):
        self.receiver_ports["tempo_http"] = http_port
        # default grpc listen port is 9095, but that conflicts with promtail.
        self.receiver_ports["tempo_grpc"] = grpc_port

        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml
        self._local_hostname = local_host

        self._supported_receivers: Tuple[ReceiverProtocol, ...] = tuple(self.receiver_ports)

    @property
    def tempo_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.receiver_ports["tempo_http"]

    def get_requested_ports(self, service_name_prefix: str) -> List[Tuple[str, int, int]]:
        """List of service names and port mappings for the kubernetes service patch."""
        # todo allow remapping ports?
        return [
            (
                (service_name_prefix + protocol).replace("_", "-"),
                self.receiver_ports[protocol],
                self.receiver_ports[protocol],
            )
            for protocol in self._supported_receivers
        ]

    @property
    def host(self) -> str:
        """Hostname at which tempo is running."""
        return socket.getfqdn()

    @property
    def receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """All receivers supported by this Tempo client."""
        return self._supported_receivers

    def get_config(self, receivers: Sequence[ReceiverProtocol]) -> str:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        return yaml.safe_dump(
            {
                "auth_enabled": False,
                "search_enabled": True,
                "server": {
                    "http_listen_port": self.tempo_port,
                    "grpc_listen_port": self.receiver_ports["tempo_grpc"],
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
                    "max_block_duration": "5m",
                },
                "compactor": {
                    "compaction": {
                        # blocks in this time window will be compacted together
                        "compaction_window": "1h",
                        # maximum size of compacted blocks
                        "max_compaction_objects": 1000000,
                        "block_retention": "1h",
                        "compacted_block_retention": "10m",
                        "flush_size_bytes": 5242880,
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
                            # where to store the the wal locally
                            "path": self.wal_path
                        },
                        "pool": {
                            # number of traces per index record
                            "max_workers": 100,
                            "queue_depth": 10000,
                        },
                    }
                },
            }
        )

    @property
    def pebble_layer(self) -> Layer:
        """Generate the pebble layer for the Tempo container."""
        return Layer(
            {
                "services": {
                    "tempo": {
                        "override": "replace",
                        "summary": "Main Tempo layer",
                        "command": '/bin/sh -c "/tempo -config.file={} | tee {}"'.format(
                            self.config_path, self.log_path
                        ),
                        "startup": "enabled",
                    }
                },
            }
        )

    def is_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        # Fixme: sometimes it takes a few attempts for it to report ready.
        try:
            out = getoutput(f"curl http://{self._local_hostname}:{self.tempo_port}/ready").split(
                "\n"
            )[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    @staticmethod
    def _build_receivers_config(receivers: Sequence[ReceiverProtocol]):  # noqa: C901
        receivers_set = set(receivers)  # convert to set for faster lookup

        config = {}

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
