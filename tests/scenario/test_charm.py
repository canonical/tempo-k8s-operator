from pathlib import Path

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import Container, State
from scenario.sequences import check_builtin_sequences

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(params=(True, False))
def base_state(request):
    return State(leader=request.param, containers=[Container("tempo", can_connect=False)])


def test_builtin_sequences(tempo_charm, base_state):
    with charm_tracing_disabled():
        check_builtin_sequences(tempo_charm, template_state=base_state)


def test_start(context, base_state):
    # verify the charm runs at all with and without leadership
    with charm_tracing_disabled():
        context.run("start", base_state)
