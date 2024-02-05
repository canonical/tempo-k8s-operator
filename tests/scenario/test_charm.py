from pathlib import Path

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import State
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
