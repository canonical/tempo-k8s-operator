from typing import Dict
from unittest.mock import patch

import pytest
import scenario
from scenario.state import next_relation_id

from coordinator import RECOMMENDED_DEPLOYMENT, MINIMAL_DEPLOYMENT
from tempo_cluster import TempoRole, TempoClusterRequirerAppData, TempoClusterRequirerUnitData, JujuTopology

SCALABLE_MODE = "scalable-single-binary"
MONOLITH_MODE = "all"


def assert_tempo_running_with_target(target: str, state: scenario.State):
    tempo_layer_cmd = state.get_container("tempo").plan.to_dict()['services']['tempo']['command']
    assert f"-target {target}" in tempo_layer_cmd


def assert_tempo_not_running(state: scenario.State):
    tempo_svc = state.get_container("tempo").services.get('tempo')
    assert not tempo_svc or not tempo_svc.is_running()


def assert_status(state: scenario.State, unit: str = None, app: str = None, message: str = None):
    if unit:
        status = state.unit_status
        assert status.name == unit
        if message:
            assert status.message == message

    if app:
        status = state.app_status
        assert status.name == app
        if message:
            assert status.message == message


@pytest.fixture
def peers():
    return scenario.PeerRelation("tempo-peers", peers_data={0: {}, 1: {}})


@pytest.fixture
def cluster():
    return scenario.Relation("tempo-cluster")


@pytest.fixture
def s3():
    return scenario.Relation(
        "s3",
        remote_app_data={
            "access-key": "key",
            "bucket": "tempo",
            "endpoint": "http://1.2.3.4:9000",
            "secret-key": "soverysecret",
        },
        local_unit_data={"bucket": "tempo"},
    )


@pytest.fixture
def mock_tempo_workload_ready():
    with patch("tempo.Tempo.is_ready", new=lambda _: True):
        yield


@pytest.fixture
def tempo_container_ready(context, mock_tempo_workload_ready):
    tempo = scenario.Container("tempo", can_connect=True)
    return context.run(
        tempo.pebble_ready_event,
        scenario.State(
            leader=True,
            containers=[tempo]
        )).get_container("tempo")


def test_monolithic(context, tempo_container_ready):
    state = scenario.State(
        leader=True,
        containers=[tempo_container_ready]
    )
    state_out = context.run("update-status", state)

    assert_tempo_running_with_target(MONOLITH_MODE, state_out)
    assert_status(state_out, unit="active")


def test_scaling_no_s3_inconsistent(context, tempo_container_ready, peers):
    state = scenario.State(
        leader=True,
        containers=[tempo_container_ready],
        relations=[peers]
    )

    # it doesn't actually matter what event we're running here
    state_out = context.run("update-status", state)
    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


def test_clustered_and_scaled_coordinator_no_s3_inconsistent(context, peers, cluster, tempo_container_ready):
    state = scenario.State(
        leader=True,
        containers=[tempo_container_ready],
        relations=[peers, cluster]
    )

    # it doesn't actually matter what event we're running here
    state_out = context.run("update-status", state)
    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


def test_clustered_coordinator_no_s3_inconsistent(context, tempo_container_ready, cluster):
    state = scenario.State(
        leader=True,
        containers=[tempo_container_ready],
        relations=[cluster]
    )
    state_out = context.run("update-status", state)
    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


def test_single_coordinator_no_s3_no_worker_inconsistent(context, tempo_container_ready, cluster):
    state = scenario.State(
        leader=True,
        config={"coordinator_runs_workload_when_clustered": False},
        containers=[tempo_container_ready],
        relations=[cluster]
    )
    state_out = context.run("update-status", state)
    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


@pytest.fixture
def s3_ready_state(context, tempo_container_ready, peers, s3):
    state = scenario.State(
        leader=True,
        containers=[tempo_container_ready],
        relations=[s3]
    )
    # we need to run s3-changed to have tempo push the configs to disk and be ready to
    # run in scalable/distributed mode
    return context.run(s3.changed_event, state)


def test_scaling_with_s3_consistent(context, s3_ready_state, tempo_container_ready, s3, peers):
    state = s3_ready_state.replace(
        relations=[peers, s3]
    )
    with context.manager(peers.joined_event, state) as mgr:
        assert mgr.charm._is_consistent
        state_out = mgr.run()
    assert_tempo_running_with_target(SCALABLE_MODE, state_out)
    assert_status(state_out, unit="active")


def test_clustered_scaling_with_s3_consistent(context, s3_ready_state, tempo_container_ready, s3, peers, cluster):
    state = s3_ready_state.replace(
        relations=[peers, s3, cluster]
    )

    # doesn't matter what event we're processing...
    for event in [cluster.joined_event, peers.joined_event]:
        state_out = context.run(event, state)
        assert_tempo_running_with_target(SCALABLE_MODE, state_out)
        assert_status(state_out, unit="active")


def test_clustered_with_s3_consistent(context, s3_ready_state, tempo_container_ready, s3, cluster):
    state = s3_ready_state.replace(
        relations=[s3, cluster]
    )

    state_out = context.run(cluster.joined_event, state)
    assert_tempo_running_with_target(SCALABLE_MODE, state_out)
    assert_status(state_out, unit="active")


def test_coord_only_scaling_with_s3_consistent(context, s3_ready_state, tempo_container_ready, s3, peers):
    state = s3_ready_state.replace(
        relations=[peers, s3]
    )

    with context.manager(s3.changed_event, state) as mgr:
        assert not mgr.charm.tempo_cluster.has_workers
        # coord is also worker because we don't have a cluster relation
        assert mgr.charm.coordinator._is_worker

        # still coherent, because we're running the worker!
        assert mgr.charm.coordinator.is_coherent
        assert mgr.charm._is_consistent
        state_out = mgr.run()

    assert_tempo_running_with_target(SCALABLE_MODE, state_out)
    assert_status(state_out, unit="active")


def test_coord_only_clustered_scaling_with_s3_inconsistent(context, tempo_container_ready, s3, peers, cluster,
                                                           s3_ready_state):
    state = s3_ready_state.replace(
        config={"coordinator_runs_workload_when_clustered": False},
        relations=[peers, s3, cluster]
    )

    # doesn't quite matter what event we're processing
    with context.manager("update-status", state) as mgr:
        # we have a cluster relation -> we have a worker
        assert mgr.charm.tempo_cluster.has_workers

        # coord is NOT worker because we have a cluster relation
        # we're configured to stop worker when we have a cluster
        # fixme do we have a good reason to only stop the worker when we're clustered,
        #  instead of when we're clustered AND coherent?
        assert not mgr.charm.coordinator._is_worker

        # not coherent, because we're running the worker and the cluster doesn't have all roles
        assert not mgr.charm.coordinator.is_coherent
        assert not mgr.charm._is_consistent
        state_out = mgr.run()

    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


def test_coord_only_clustered_with_s3_inconsistent(context, tempo_container_ready, s3, cluster, s3_ready_state):
    state = s3_ready_state.replace(
        config={"coordinator_runs_workload_when_clustered": False},
        relations=[s3, cluster]
    )

    # doesn't quite matter what event we're processing
    state_out = context.run("update-status", state)
    assert_tempo_not_running(state_out)
    assert_status(state_out, unit="blocked")


def generate_cluster_relations(deployment_spec: Dict[TempoRole, int], template: scenario.Relation):
    cluster_relations = []
    for role, n in deployment_spec.items():
        c = template.replace(
            relation_id=next_relation_id(),
            remote_app_data=TempoClusterRequirerAppData(role=role).dump(),
            remote_units_data={
                i: TempoClusterRequirerUnitData(
                    juju_topology=JujuTopology(
                        model="model-name",
                        unit=str(i)),
                    address=f"0.0.0.{i}"
                ).dump() for i in range(n)
            }
        )
        cluster_relations.append(c)
    return cluster_relations


def test_full_clustering_consistent(context, tempo_container_ready, s3, cluster, s3_ready_state):
    """The happy path of all happy paths."""
    state = s3_ready_state.replace(
        config={"coordinator_runs_workload_when_clustered": False},
        relations=[s3, *generate_cluster_relations(MINIMAL_DEPLOYMENT, cluster)]
    )

    # doesn't quite matter what event we're processing
    state_out = context.run("update-status", state)
    assert_tempo_running_with_target(SCALABLE_MODE, state_out)
    assert_status(state_out, unit="active", message="[coordinator] degraded")


def test_full_clustering_recommended(context, tempo_container_ready, s3, cluster, s3_ready_state):
    state = s3_ready_state.replace(
        config={"coordinator_runs_workload_when_clustered": False},
        relations=[s3, *generate_cluster_relations(RECOMMENDED_DEPLOYMENT, cluster)]
    )

    # doesn't quite matter what event we're processing
    state_out = context.run("update-status", state)
    assert_tempo_running_with_target(SCALABLE_MODE, state_out)
    assert_status(state_out, unit="active", message="")
