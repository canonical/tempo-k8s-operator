import pytest

from tempo import Tempo


@pytest.mark.parametrize(
    "protocols, expected_config",
    (
            (
                    (
                            "otlp_grpc",
                            "otlp_http",
                            "zipkin",
                            "tempo",
                            "jaeger_http_thrift",
                            "jaeger_grpc",
                            "opencensus",
                            "jaeger_thrift_compact",
                            "jaeger_thrift_http",
                            "jaeger_thrift_binary",
                    ),
                    {
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
                    },
            ),
            (
                    ("otlp_http", "zipkin", "tempo", "jaeger_thrift_compact"),
                    {
                        "jaeger": {
                            "protocols": {
                                "thrift_compact": None,
                            }
                        },
                        "zipkin": None,
                        "otlp": {"protocols": {"http": None}},
                    },
            ),
            ([], {}),
    ),
)
def test_tempo_receivers_config(protocols, expected_config):
    assert Tempo._build_receivers_config(protocols) == expected_config
