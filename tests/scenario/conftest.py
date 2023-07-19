from unittest.mock import patch

import pytest
from scenario import Context

from charm import TempoCharm


@pytest.fixture
def tempo_charm():
    with patch("charm.KubernetesServicePatch"), patch("lightkube.core.client.GenericSyncClient"):
        yield TempoCharm


@pytest.fixture(scope="function")
def context(tempo_charm):
    return Context(charm_type=tempo_charm)
