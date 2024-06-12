import json
import os
from unittest.mock import patch

import pytest
import scenario
from catan import (
    App, Catan
)
from scenario import Container, State

os.environ["CHARM_TRACING_ENABLED"] = "0"


def get_facade(name="facade"):
    facade = App.from_path(
        "/home/pietro/canonical/charm-relation-interfaces/facade-charm",
        name=name,
    )
    return facade


@pytest.fixture
def s3_facade() -> App:
    return get_facade("s3")


def prep_s3_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={
                "endpoint": json.dumps("provides-s3"),
                "app_data": json.dumps({
                    "access-key": "key",
                    "bucket": "tempo",
                    "endpoint": "http://1.2.3.4:9000",
                    "secret-key": "soverysecret",
                })
            }
        ),
        catan.get_app("s3")
    )


@pytest.fixture
def ca_facade() -> App:
    return get_facade("ca")


def prep_ca_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={
                "endpoint": json.dumps("provides-certificates"),
                "app_data": json.dumps("")
            }
        ),
        catan.get_app("ca")
    )


@pytest.fixture
def traefik_facade() -> App:
    return get_facade("traefik")


def prep_traefik_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={
                "endpoint": json.dumps("provides-traefik_route"),
                "app_data": json.dumps({"external_host": "1.2.3.4", "scheme": "http"})
            }
        ),
        catan.get_app("ca")
    )


@pytest.fixture
def tempo_coordinator():
    tempo = App.from_path(
        "/home/pietro/canonical/tempo-k8s",
        patches=[patch("charm.KubernetesServicePatch"),
                 patch("lightkube.core.client.GenericSyncClient"),
                 patch("charm.TempoCharm._update_server_cert"),
                 patch("tempo.Tempo.is_ready", new=lambda _: True),
                 ],
        name="tempo",
    )
    yield tempo


@pytest.fixture
def tempo_worker():
    tempo = App.from_path(
        "/home/pietro/canonical/tempo-worker-k8s-operator",
        patches=[patch("charm.KubernetesServicePatch")],
        name="tempo",
    )
    yield tempo


@pytest.fixture
def tempo_coordinator_state():
    return State(
        containers=[
            Container(
                name="tempo",
                can_connect=True
            )
        ],
    )


@pytest.fixture
def tempo_worker_state():
    return State(
        containers=[
            Container(
                name="tempo",
                can_connect=True
            )
        ],
    )


def assert_active(state: State, message=None):
    assert state.unit_status.name == 'active'
    if message:
        assert state.unit_status.message == message


def assert_blocked(state: State, message=None):
    assert state.unit_status.name == 'blocked'
    if message:
        assert state.unit_status.message == message


def test_monolithic_deployment(tempo_coordinator, tempo_coordinator_state):
    c = Catan()
    c.deploy(tempo_coordinator, [0], state_template=tempo_coordinator_state)

    ms_out = c.settle()

    state_out = ms_out.get_unit_state(tempo_coordinator, 0)
    assert_active(state_out, "")


@pytest.mark.xfail  # catan doesn't support peer relations yet
def test_scaling_without_s3_blocks(tempo_coordinator, tempo_coordinator_state, s3_facade):
    c = Catan()
    c.deploy(tempo_coordinator, [0, 1], state_template=tempo_coordinator_state)

    ms_out = c.settle()

    assert_blocked(ms_out.get_unit_state(tempo_coordinator, 0))
    assert_blocked(ms_out.get_unit_state(tempo_coordinator, 1))


def test_scaling_with_s3_active(tempo_coordinator, tempo_coordinator_state, s3_facade):
    c = Catan()
    c.deploy(tempo_coordinator, [0], state_template=tempo_coordinator_state)
    c.deploy(s3_facade, [0])
    prep_s3_facade(c)
    c.integrate(tempo_coordinator, "s3", s3_facade, "provide-s3")
    c.add_unit(tempo_coordinator, 1, tempo_coordinator_state)

    ms_out = c.settle()

    assert_active(ms_out.get_unit_state(tempo_coordinator, 0))
    assert_active(ms_out.get_unit_state(tempo_coordinator, 1))
