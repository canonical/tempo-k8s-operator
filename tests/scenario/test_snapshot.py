import ops
import scenario
from charms.tempo_k8s.v0.snapshot import get_state


def test_get_null_state():
    ctx = scenario.Context(ops.CharmBase, meta={"name": "tango"})
    with ctx.manager("start", state=scenario.State()) as mgr:
        get_state(mgr.charm)


def test_get_leader_state():
    ctx = scenario.Context(ops.CharmBase, meta={"name": "tango"})
    with ctx.manager("start", state=scenario.State(leader=True)) as mgr:
        assert get_state(mgr.charm).leader


def test_get_relation_state():
    ctx = scenario.Context(
        ops.CharmBase, meta={"name": "tango", "requires": {"foo": {"interface": "bar"}}}
    )
    with ctx.manager(
        "start",
        state=scenario.State(
            relations=[scenario.Relation(endpoint="foo", remote_app_data={"foo": "bar"})]
        ),
    ) as mgr:
        relation = get_state(mgr.charm).relations[0]
        assert relation.endpoint == "foo"
        assert relation.remote_app_data["foo"] == "bar"
