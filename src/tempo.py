#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""

import socket
from subprocess import CalledProcessError, getoutput
from typing import List, Tuple

import yaml
from charms.tempo_k8s.v1.tracing import RawIngester
from ops.pebble import Layer


class Tempo:
    """Class representing the Tempo client workload configuration."""

    config_path = "/etc/tempo.yaml"
    wal_path = "/etc/tempo_wal"
    log_path = "/var/log/tempo.log"

    def __init__(
        self, port: int = 3200, grpc_listen_port: int = 9096, local_host: str = "0.0.0.0"
    ):
        self.tempo_port = port

        # default grpc listen port is 9095, but that conflicts with promtail.
        self.grpc_listen_port = grpc_listen_port

        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml
        # todo make configurable?
        self.otlp_grpc_port = 4317
        self.otlp_http_port = 4318
        self.zipkin_port = 9411
        self.jaeger_http_thrift_port = 14268
        self.jaeger_grpc_port = 14250
        self._local_hostname = local_host

        self._supported_ingesters: Tuple[RawIngester, ...] = (
            ("tempo", self.tempo_port),
            ("otlp_grpc", self.otlp_grpc_port),
            ("otlp_http", self.otlp_http_port),
            ("zipkin", self.zipkin_port),
            ("jaeger_http_thrift", self.jaeger_http_thrift_port),
            ("jaeger_grpc", self.jaeger_grpc_port),
        )

    def get_requested_ports(self, service_name_prefix: str):
        """List of service names and port mappings for the kubernetes service patch."""
        # todo allow remapping ports?
        return [
            ((service_name_prefix + protocol).replace("_", "-"), port, port)
            for protocol, port in self._supported_ingesters
        ]

    @property
    def host(self) -> str:
        """Hostname at which tempo is running."""
        return socket.getfqdn()

    @property
    def ingesters(self) -> List[RawIngester]:
        """All ingesters supported by this Tempo client."""
        return [(protocol, port) for protocol, port in self._supported_ingesters]

    def get_config(self) -> str:
        """Generate the Tempo configuration."""
        return yaml.safe_dump(
            {
                "auth_enabled": False,
                "search_enabled": True,
                "server": {
                    "http_listen_port": self.tempo_port,
                    "grpc_listen_port": self.grpc_listen_port,
                },
                # this configuration will listen on all ports and protocols that tempo is capable of.
                # the receives all come from the OpenTelemetry collector.  more configuration information can
                # be found there: https://github.com/open-telemetry/opentelemetry-collector/tree/overlord/receiver
                #
                # for a production deployment you should only enable the receivers you need!
                # todo: provider should request specific protocols in its app databag, and tempo should only activate the receivers it needs.
                "distributor": {
                    "receivers": {
                        "jaeger": {
                            "protocols": {
                                "thrift_http": None,
                                "grpc": None,
                                "thrift_binary": None,
                                "thrift_compact": None,
                            }
                        },
                        "zipkin": None,
                        "otlp": {"protocols": {"http": None, "grpc": None}},
                        "opencensus": None,
                    }
                },
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
