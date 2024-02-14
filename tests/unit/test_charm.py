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
                "jaeger_thrift_http",
                "jaeger_thrift_http",
            ),
            {
                "jaeger": {
                    "protocols": {
                        "grpc": None,
                        "thrift_http": None,
                    }
                },
                "zipkin": None,
                "otlp": {"protocols": {"http": None, "grpc": None}},
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": None,
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
    assert Tempo(None)._build_receivers_config(protocols) == expected_config
