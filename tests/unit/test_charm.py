from scenario import Scenario, Runtime

Runtime.install()

from pathlib import Path
from unittest import mock

import pytest
from charm import TempoCharm
from ops.charm import StartEvent
from scenario.structs import CharmSpec, Scene, event, Context, State

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope='session')
def scenario():
    with mock.patch("charm.KubernetesServicePatch", lambda x, y: None):
        yield Scenario(
            CharmSpec.load(TempoCharm)
        )


@pytest.fixture(params=(True, False))
def base_context(request):
    return Context(
        state=State(
            leader=request.param
        )
    )


def test_start(scenario, base_context):
    out = scenario.run(
        Scene(
            event("start"),
            context=base_context)
    )
    assert isinstance(out.charm, TempoCharm)
    assert isinstance(out.event, StartEvent)
    assert out.charm.unit.name == "tempo-k8s/0"
    assert out.charm.model.uuid == base_context.state.model.uuid
