import yaml


class Tempo:
    config_path = "/etc/tempo.yaml"
    wal_path = '/etc/tempo_wal'

    def __init__(self, port: int):
        self._port = port

    def get_config(self):
        return yaml.safe_dump(
            {'auth_enabled': False,
             'server': {
                 'http_listen_port': self._port
             },
             # this configuration will listen on all ports and protocols that tempo is capable of.
             # the receives all come from the OpenTelemetry collector.  more configuration information can
             # be found there: https://github.com/open-telemetry/opentelemetry-collector/tree/master/receiver
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
