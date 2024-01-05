import json
import socket
from pathlib import Path

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import Relation, State
from scenario.sequences import check_builtin_sequences

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


def test_builtin_sequences(tempo_charm):
    with charm_tracing_disabled():
        check_builtin_sequences(tempo_charm)


@pytest.fixture(params=(True, False))
def base_state(request):
    return State(leader=request.param)


def test_start(context, base_state):
    # verify the charm runs at all with and without leadership
    with charm_tracing_disabled():
        context.run("start", base_state)


def test_tempo_endpoint_published(context):
    tracing = Relation("tracing")
    state = State(leader=True, relations=[tracing])

    with charm_tracing_disabled():
        out = context.run(tracing.created_event, state)

    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "ingesters": '[{"protocol": "tempo", "port": 3200}, '
        '{"protocol": "otlp_grpc", "port": 4317}, '
        '{"protocol": "otlp_http", "port": 4318}, '
        '{"protocol": "zipkin", "port": 9411}, '
        '{"protocol": "jaeger_http_thrift", "port": 14268}, '
        '{"protocol": "jaeger_grpc", "port": 14250}]',
        "host": json.dumps(socket.getfqdn()),
    }
