from unittest.mock import patch

import ops
import pytest
from scenario import Container, PeerRelation, Relation, State


def test_monolithic_status_no_s3(context):
    state_out = context.run(
        "start", State(unit_status=ops.ActiveStatus(), containers=[Container("tempo")])
    )
    assert state_out.unit_status.name == "waiting"


def test_scaled_status_no_s3(context):
    state_out = context.run(
        "start",
        State(
            relations=[PeerRelation("tempo-peers", peers_data={1: {}, 2: {}})],
            unit_status=ops.ActiveStatus(),
            containers=[Container("tempo")],
        ),
    )
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_with_s3(context):
    state_out = context.run(
        "start",
        State(
            relations=[
                PeerRelation("tempo-peers", peers_data={1: {}, 2: {}}),
                Relation(
                    "s3",
                    remote_app_data={
                        "access-key": "key",
                        "bucket": "tempo",
                        "endpoint": "http://1.2.3.4:9000",
                        "secret-key": "soverysecret",
                    },
                    local_unit_data={"bucket": "tempo"},
                ),
            ],
            unit_status=ops.ActiveStatus(),
            containers=[Container("tempo")],
        ),
    )
    assert state_out.unit_status.name == "waiting"


@pytest.mark.parametrize("leader", (True, False))
@patch("tempo.Tempo.is_ready", new=lambda _: True)
def test_scaled_status_with_s3_and_container_ready(context, leader):
    container = Container(
        name="tempo",
        can_connect=True,
    )
    state_out = context.run(
        container.pebble_ready_event,
        State(
            leader=leader,
            relations=[
                PeerRelation("tempo-peers", peers_data={1: {}, 2: {}}),
                Relation(
                    "s3",
                    remote_app_data={
                        "access-key": "key",
                        "bucket": "tempo",
                        "endpoint": "http://1.2.3.4:9000",
                        "secret-key": "soverysecret",
                    },
                    local_unit_data={"bucket": "tempo"},
                ),
            ],
            unit_status=ops.ActiveStatus(),
            containers=[container],
        ),
    )
    assert state_out.unit_status.name == "active"
    assert state_out.get_container("tempo").services["tempo"].is_running()
