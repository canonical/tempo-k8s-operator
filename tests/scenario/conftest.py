from unittest.mock import patch

import pytest
from scenario import Context

from charm import TempoCharm


@pytest.fixture
def tempo_charm():
    with patch("charm.KubernetesServicePatch"):
        with patch("lightkube.core.client.GenericSyncClient"):
            with patch("charm.TempoCharm._update_server_cert"):
                yield TempoCharm


@pytest.fixture(scope="function")
def context(tempo_charm):
    return Context(charm_type=tempo_charm)
