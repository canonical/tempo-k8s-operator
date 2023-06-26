import socket

import yaml
from charms.tempo_k8s.v0.tempo_scrape import Ingester
from subprocess import getoutput, CalledProcessError


class Tempo:
    config_path = "/etc/tempo.yaml"
    wal_path = '/etc/tempo_wal'

    def __init__(self, port: int = 3200, local_host: str = "0.0.0.0"):
        self.tempo_port = port

        # todo make configurable?
        self.otlp_grpc_port = 4317
        self.otlp_http_port = 4318
        self.zipkin_port = 9411

        self._local_hostname = local_host

        self._supported_ingesters = (
            ("tempo", self.tempo_port),
            ("otlp_grpc", self.otlp_grpc_port),
            ("otlp_http", self.otlp_http_port),
            ("zipkin", self.zipkin_port),
        )

    def get_requested_ports(self, service_name_prefix: str):
        # todo allow remapping ports?
        return [
            (service_name_prefix + ingester_type, ingester_port, ingester_port)
            for ingester_type, ingester_port in self._supported_ingesters
        ]

    @property
    def host(self) -> str:
        return socket.getfqdn()

    @property
    def ingesters(self):
        return [Ingester(type=_type, port=port) for _type, port in self._supported_ingesters]

    def get_config(self):
        return yaml.safe_dump(
            {'auth_enabled': False,
             'search_enabled': True,
             'server': {
                 'http_listen_port': self.tempo_port
             },
             # this configuration will listen on all ports and protocols that tempo is capable of.
             # the receives all come from the OpenTelemetry collector.  more configuration information can
             # be found there: https://github.com/open-telemetry/opentelemetry-collector/tree/overlord/receiver
             #
             # for a production deployment you should only enable the receivers you need!
             'distributor': {'receivers': {'jaeger': {
                 'protocols': {'thrift_http': None,
                               'grpc': None,
                               'thrift_binary': None,
                               'thrift_compact': None}},
                 'zipkin': None,
                 'otlp': {'protocols': {'http': None, 'grpc': None}},
                 'opencensus': None}},
             # the length of time after a trace has not received spans to consider it complete and flush it
             # cut the head block when it his this number of traces or ...
             #   this much time passes
             'ingester': {'trace_idle_period': '10s',
                          'max_block_bytes': 100,
                          'max_block_duration': '5m'},
             'compactor': {'compaction': {
                 # blocks in this time window will be compacted together
                 'compaction_window': '1h',
                 # maximum size of compacted blocks
                 'max_compaction_objects': 1000000,
                 'block_retention': '1h',
                 'compacted_block_retention': '10m',
                 'flush_size_bytes': 5242880}},

             # see https://grafana.com/docs/tempo/latest/configuration/#storage
             'storage': {
                 'trace': {
                     # FIXME: only good for testing# backend configuration to use;
                     #  one of "gcs", "s3", "azure" or "local"

                     'backend': 'local',
                     'local': {
                         'path': '/traces'
                     },
                     'wal': {
                         # where to store the the wal locally
                         'path': self.wal_path
                     },
                     'pool': {
                         # number of traces per index record
                         'max_workers': 100,
                         'queue_depth': 10000}}
             }
             }
        )

    def is_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        try:
            out = getoutput(f"curl http://{self._local_hostname}:{self.tempo_port}/ready").split(
                "\n"
            )[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == 'ready'
