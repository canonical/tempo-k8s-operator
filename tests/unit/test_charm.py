from pathlib import Path
from unittest import mock

import pytest
from ops.charm import StartEvent
from scenario import Context, State

from charm import TempoCharm

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope='session')
def charm_type():
    with mock.patch("charm.KubernetesServicePatch", lambda x, y: None):
        yield TempoCharm


@pytest.fixture
def base_context(charm_type):
    return Context(charm_type)


@pytest.mark.parametrize('leader', (True, False))
def test_start(base_context, leader):
    out = base_context.run(
        "start",
        State(leader=leader)
    )
    assert isinstance(out.charm, TempoCharm)
    assert isinstance(out.event, StartEvent)
    assert out.charm.unit.name == "tempo-k8s/0"
    assert out.charm.model.uuid == base_context.state.model.uuid
