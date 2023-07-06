import os
import socket
from pathlib import Path

import pytest
from charms.tempo_k8s.v0.charm_instrumentation import CHARM_TRACING_ENABLED
from scenario import Relation, State
from scenario.sequences import check_builtin_sequences

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


os.environ[CHARM_TRACING_ENABLED] = "0"


def test_builtin_sequences(tempo_charm):
    check_builtin_sequences(tempo_charm)


@pytest.fixture(params=(True, False))
def base_state(request):
    return State(leader=request.param)


def test_start(context, base_state):
    context.run("start", base_state)
    # verify the charm runs at all with and without leadership


def test_tempo_endpoint_published(context):
    tracing = Relation("tracing")
    state = State(leader=True, relations=[tracing])

    out = context.run(tracing.created_event, state)

    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "ingesters": '[{"port": "3200", "type": "tempo"}, '
        '{"port": "4317", "type": "otlp_grpc"}, '
        '{"port": "4318", "type": "otlp_http"}, '
        '{"port": "9411", "type": "zipkin"}]',
        "hostname": socket.getfqdn(),
    }
