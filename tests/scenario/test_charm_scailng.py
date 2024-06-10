import ops
from scenario import Container, State


def test_follower_unit_status_no_s3(context):
    state_out = context.run(
        "start", State(unit_status=ops.ActiveStatus(), containers=[Container("tempo")])
    )
    assert state_out.unit_status.name == "blocked"
